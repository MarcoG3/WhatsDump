import subprocess
import os, stat, platform
import time
import logging
import re
import requests, zipfile

from clint.textui import progress

logger = logging.getLogger('WhatsDump')


class CommandType:
    PLATFORM_TOOLS = 1,
    TOOLS = 2,
    TOOLS_BIN = 3


class AndroidSDK:
    AVD_NAME = 'WhatsDump'

    def __init__(self):
        self._sdk_path = os.path.abspath('android-sdk')
        self._env = self._get_env_vars()

        # Update original environment var
        os.environ['ANDROID_HOME'] = self._env['ANDROID_HOME']

    def install(self):
        # Create android-sdk/ directory and download latest SDK
        if not os.path.exists('android-sdk'):
            try:
                os.makedirs('android-sdk')
            except OSError:
                logger.error('Could not create android-sdk/ directory')
                return False

        if not self._download('android-sdk'):
            return False

        # Update SDK from sdkmanager
        logger.info('Updating SDK from manager...')

        s0 = self._run_cmd_sdkmanager("--update", show=True)

        if s0.returncode != 0:
            logger.error('Could not update SDK Manager')
            return False

        # Accept licenses
        logger.info('Accepting SDK licenses..')
        s1 = self._run_cmd_sdkmanager("--licenses", input='y\n'*20, show=True)

        if s1.returncode != 0:
            logger.error('Could not accept SDK Manager licenses')
            return False

        # List all packages to check HAXM is supported
        s2 = self._run_cmd_sdkmanager("--list")
        s2_out, s2_err = s2.communicate()

        if s2.returncode != 0:
            logger.error("Could not list SDK Manager packages")
            return False

        # Install required packages
        install_args = '--install emulator platform-tools platforms;android-23 system-images;android-23;google_apis;x86'

        if s2_out and s2_out.find('extras;intel;Hardware_Accelerated_Execution_Manager') != -1:
            install_args += ' extras;intel;Hardware_Accelerated_Execution_Manager'

        logger.info('Installing packages from SDK Manager...')
        s3 = self._run_cmd_sdkmanager(install_args, input='y\n'*20, show=True)

        if s3.returncode != 0:
            logger.error('Could not install packages from SDK Manager')
            return False

        # Create AVD
        logger.info('Creating AVD image...')
        s4 = self._run_cmd_avdmanager('create avd --force --name %s -k system-images;android-23;google_apis;x86' % self.AVD_NAME,
                           input='no\n', show=True)

        if s4.returncode != 0:
            logger.error('Could not create %s AVD from AVD Manager', self.AVD_NAME)
            return False

        return True

    def start_adb(self, port=5037):
        return self._run_cmd_adb('-P %d start-server' % port).returncode == 0

    def stop_adb(self):
        return self._run_cmd_adb('kill-server').returncode == 0

    def start_emulator(self, adb_client, show_screen, no_accel):
        emulator_device = None
        params = '-avd %s -no-boot-anim -noaudio -no-snapshot -partition-size 2047 '

        # Stop any running instance of WhatsDump AVD
        #self.stop_emulator(adb_client)

        # Snapshot of currently running devices
        devices_snap = adb_client.devices()

        # Disable hardware acceleration if asked to
        if no_accel:
            params += '-no-accel -gpu on '

        # Start emulator
        proc = self._run_cmd_emulator(params % self.AVD_NAME, show_screen,
                                      wait=False, show=True)

        # Check if any emulator connects to ADB
        while not emulator_device:
            if proc.returncode != None:
                if proc.returncode != 0:
                    logger.error('Emulator process returned an error')
                    return False

                break

            new_devices = list(set(adb_client.devices()) - set(devices_snap))

            for device in new_devices:
                if device.serial.find('emulator') != -1:
                    emulator_device = device
                    break

        # Wait boot to complete
        while True:
            try:
                if emulator_device.shell('getprop dev.bootcomplete').rstrip() == '1':
                    logger.debug('Emulator boot process completed')
                    break
            except RuntimeError:
                pass

            time.sleep(1)

        return emulator_device

    def stop_emulator(self, adb_client):
        devices = adb_client.devices()

        for device in devices:
            if device.serial.find('emulator') != -1:
                return self._run_cmd_adb('-s %s emu kill' % device.serial).returncode == 0

        return False

    def is_avd_installed(self):
        try:
            process = self._run_cmd_avdmanager('list avd')
        except:
            return False

        if process.returncode != 0:
            logger.debug('avdmanager list avd command return code: %d', process.returncode)
            return False

        for line in process.stdout:
            if line.find(self.AVD_NAME.encode()) != -1:
                return True

        return False

    def _download(self, extract_dir):
        output_zip = os.path.join(extract_dir, 'tools.zip')
        tools_dir = os.path.join(extract_dir, 'tools')

        if os.path.exists(tools_dir):
            logger.info('SDK tools directory already exists, skipping download & extraction...')
            return True

        if not os.path.isfile(output_zip):
            logger.info('Downloading and installing Android SDK...')

            # Download
            r = requests.get('https://web.archive.org/web/20190403122148/https://developer.android.com/studio/')

            if r.status_code != 200:
                logger.error('Failed GET request to developer.android.com')
                return False

            sdk_re = re.search(r'https://dl.google.com/android/repository/sdk-tools-' + platform.system().lower() + '-(\d+).zip', r.text)

            if not sdk_re:
                logger.error('Failed regex matching to find latest Android SDK (platform %s)', platform.system())
                return False

            r = requests.get(sdk_re.group(), stream=True)

            logger.info('Android SDK url found: %s', sdk_re.group())

            with open(output_zip, 'wb') as f:
                total_length = int(r.headers.get('Content-Length'))
                for chunk in progress.bar(r.iter_content(chunk_size=1024), expected_size=(total_length / 1024) + 1):
                    if chunk:
                        f.write(chunk)
                        f.flush()

            logger.info('Extracting...')
        else:
            logger.info('Android Tools already downloaded, extracting...')

        # Extraction
        z = zipfile.ZipFile(output_zip)
        z.extractall(extract_dir)

        logger.info('Android SDK successfully extracted in android-sdk/')

        return True

    def _run_cmd_sdkmanager(self, args, wait=True, input=None, show=False):
        return self._run_cmd(CommandType.TOOLS_BIN, 'sdkmanager', args, wait, input, show)

    def _run_cmd_avdmanager(self, args, wait=True, input=None, show=False):
        return self._run_cmd(CommandType.TOOLS_BIN, 'avdmanager', args, wait, input, show)

    def _run_cmd_emulator(self, args, show_screen, wait=True, input=None, show=False):
        if not show_screen:
            args += ' -no-window'

        return self._run_cmd(CommandType.TOOLS, 'emulator', args, wait, input, show)

    def _run_cmd_adb(self, args, wait=True, input=None, show=False):
        return self._run_cmd(CommandType.PLATFORM_TOOLS, 'adb', args, wait, input, show)

    def _run_cmd(self, type, binary, args, wait, input, show):
        path = None
        is_windows = platform.system() == 'Windows'

        if type == CommandType.PLATFORM_TOOLS:
            path = os.path.join(self._sdk_path, 'platform-tools')
        elif type == CommandType.TOOLS:
            path = os.path.join(self._sdk_path, 'tools')
        elif type == CommandType.TOOLS_BIN:
            path = os.path.join(self._sdk_path, 'tools', 'bin')

            if is_windows:
                binary = '%s.bat' % binary

        return self._run_raw_cmd('%s %s' % (os.path.join(path, binary), args),
                                 wait, input, show)


    # TODO: log SDK installation output / errors to android-sdk/log.txt
    def _run_raw_cmd(self, cmd, wait=True, input=None, show=False):
        args = cmd.split()

        # Set executable permission on linux if not set (zipfile not preserving permissions)
        if platform.system() != 'Windows':
            self._set_executable(args[0])

        # Run process
        proc = subprocess.Popen(args, env=self._env, cwd=self._sdk_path,
                                stdin=subprocess.PIPE if input else None,
                                stdout=None if show else subprocess.PIPE,
                                stderr=None if show else subprocess.PIPE)

        if input:
            proc.stdin.write(input)
            proc.stdin.close()

        if wait:
            proc.wait()

        return proc

    def _set_executable(self, bin_path):
        bin_path = os.path.join(self._sdk_path, bin_path)
        st = os.stat(bin_path)

        if not st.st_mode & stat.S_IEXEC:
            os.chmod(bin_path, st.st_mode | stat.S_IEXEC)

    def _get_env_vars(self):
        new_env = os.environ.copy()
        new_env['ANDROID_HOME'] = self._sdk_path
        new_env['ANDROID_SDK_HOME'] = self._sdk_path
        new_env['ANDROID_SDK_ROOT'] = self._sdk_path
        new_env['ANDROID_AVD_HOME'] = os.path.join(self._sdk_path, '.android').join('avd')

        return new_env
