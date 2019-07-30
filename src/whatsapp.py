import os
import logging
import time

from adb import InstallError
from utils import suppress_stderr
from tools import ViewClientTools

logger = logging.getLogger('WhatsDump')


class WaException:
    def __init__(self, reason):
        self.reason = reason


class WhatsApp:
    def __init__(self, adb_client):
        self.adb_client = adb_client

    def extract_msgstore(self, dst_path):
        storage_paths = [
            self.adb_client.shell('echo $EXTERNAL_STORAGE'),
            '/storage/emulated/0'
        ]

        # get most recent msgstore db path
        for spath in storage_paths:
            db_path = self.adb_client.shell('ls -t %s/Whatsapp/Databases/msgstore* | head -1' % spath.rstrip()).rstrip()

            if not db_path:
                continue

            dst_full_path = os.path.join(dst_path, os.path.basename(db_path))
            logger.info('Extracting msgstore database from path: %s', db_path)

            # Returns None on success
            if self.adb_client.pull(db_path, dst_full_path) is None:
                return dst_full_path

        return None

    def extract_priv_key(self, dst_path):
        dst_full_path = os.path.join(dst_path, 'key')

        return self.adb_client.pull('/data/data/com.whatsapp/files/key', dst_full_path) is None

    def register_phone(self, msgstore_path, country_code, phone_no, verify_method, verify_callback):
        # Step 0: install culebra dependencies
        tools = ViewClientTools(self.adb_client)
        tools.install_culebra_tools()

        # Step 1: cleanup
        if not self._uninstall():
            raise WaException('Can not cleanup device')

        # Step 2: install
        logger.info('Installing WhatsApp...')

        try:
            if not self._install():
                raise WaException('Can not install WhatsApp APK')
        except InstallError, e:
            raise WaException(e.message)

        # Step 3a: create / clean /WhatsApp/ data directory
        logger.info('Cleaning WhatsApp...')
        self.adb_client.shell('rm -rf /sdcard/WhatsApp')
        self.adb_client.shell('mkdir -p /sdcard/WhatsApp/Databases')

        # Step 3b: move msgstore.db to correct location
        logger.info('Moving extracted database into emulator...')
        self.adb_client.push(msgstore_path, os.path.join('/sdcard/WhatsApp/Databases/', os.path.basename(msgstore_path)))

        # FIXME?
        vc = tools.get_viewclient()

        # Step 4: open whatsapp
        if not self._open_app():
            raise WaException('Can not open WhatsApp application')

        # Step 5: automate registration
        if not self._automate_accept_eula(vc):
            raise WaException('Can not accept EULA')

        if not self._allow_access(vc):
            logger.warning("Skipped allowing WhatsApp to access media/files")
            #raise WaException('Can not allow WhatsApp to access media/files')

        if not self._do_verify(vc, country_code, phone_no, verify_method, verify_callback):
            raise WaException('Can not verify phone number')

    def _do_verify(self, vc, cc, phone, method, code_callback):
        # Set country code
        cc_view = self._wait_views(vc, 'com.whatsapp:id/registration_cc')

        logger.info('Touching and changing country code TextEdit...')

        if not cc_view:
            return False

        cc_view.touch()
        cc_view.setText(str(cc))

        # Set phone number
        number_view = self._wait_views(vc, 'com.whatsapp:id/registration_phone')

        logger.info('Touching and changing phone number TextEdit...')

        if not number_view:
            return False

        number_view.touch()
        number_view.setText(str(phone))

        # Click "Next"
        next_view = self._wait_views(vc, 'com.whatsapp:id/registration_submit')

        logger.info('Touching registration submit button...')

        if not next_view:
            return False

        next_view.touch()

        # Confirm Dialog clicking "OK"
        # Extend timeout to be 6*5 seconds (5 minutes) because WhatsApp could take time to send code
        confirm_view = self._wait_views(vc, 'android:id/button1', max_tries=60, frequency=5)

        logger.info('Touching OK confirmation button...')

        if not confirm_view:
            return False

        confirm_view.touch()

        # Verify by call or SMS
        if method == 'sms':
            logger.info('You should receive a SMS by WhatsApp soon')
            self._verify_by_sms(vc, code_callback)
        else:
            logger.info('You should receive a call by WhatsApp soon')
            self._verify_by_call(vc, code_callback)

        # Restore messages
        gdrive_msg_view = self._wait_views(vc, 'android:id/message', max_tries=5)
        skip_btn_view = self._wait_views(vc, 'android:id/button2', max_tries=5)

        if not gdrive_msg_view and not skip_btn_view:
            logger.debug('Expected Google Drive permission dialog, ignoring..')

        # Restore messages Activity
        restore_btn_view = self._wait_views(vc, 'com.whatsapp:id/perform_restore', max_tries=30, frequency=5)

        if not restore_btn_view:
            raise WaException('Cannot find restore button, is msgcrypt associated with +%d %s?' % (cc, phone))

        logger.info('Restoring messages... (might take a while)')
        restore_btn_view.touch()

        # Wait for result (max 15 minutes)
        result_msg_view = self._wait_views(vc, 'com.whatsapp:id/msgrestore_result_box', frequency=10, max_tries=90)

        if not result_msg_view:
            raise WaException('Could not restore messages')

        logger.info('%s', result_msg_view.getText())

        return True

    def _verify_by_sms(self, vc, code_callback):
        while True:
            code = code_callback()

            if code:
                if self._try_code(vc, code):
                    return True

                logger.error('Verification code NOT valid!')
            else:
                # Attempt to re-send SMS
                logger.info('Attempting to re-send verification code...')

                try:
                    seconds_to_wait = self._get_countdown_sms(vc)

                    # Got countdown seconds, wait and request new code
                    logger.info('Waiting %d seconds to request new SMS', seconds_to_wait)
                    time.sleep(seconds_to_wait)
                except WaException:
                    pass

                # Resend SMS
                resend_view = self._wait_views(vc, 'com.whatsapp:id/resend_sms_btn')

                if not resend_view:
                    raise WaException('Cannot find resend sms button view')

                # Resend SMS button might be disabled for few hours
                if resend_view.getText().find(' in ') != -1:
                    raise WaException('Cannot request new code, try again later: %s' % resend_view.getText())

                # Touch
                resend_view = self._wait_views(vc, 'com.whatsapp:id/resend_sms_btn')
                resend_view.touch()

    def _verify_by_call(self, vc, code_callback):
        request_call = True

        while True:
            call_btn_view = self._wait_views(vc, 'com.whatsapp:id/call_btn')
            countdown_view = self._wait_views(vc, 'com.whatsapp:id/countdown_time_voice', max_tries=1)

            if not call_btn_view:
                raise WaException('Could not find call button view')

            if request_call and call_btn_view.getText().find(' in ') != -1:
                raise WaException('Cannot request new code, try again later: %s' % call_btn_view.getText())

            if request_call and countdown_view:
                try:
                    seconds_to_wait = self._get_countdown_call(vc)

                    # Got countdown seconds, wait and request new code
                    logger.info('Waiting %d seconds to request a call', seconds_to_wait)
                    time.sleep(seconds_to_wait)
                except WaException:
                    pass

            if request_call:
                # Update & Touch
                resend_view = self._wait_views(vc, 'com.whatsapp:id/call_btn')
                resend_view.touch()

                call_btn_view = self._wait_views(vc, 'com.whatsapp:id/call_btn')
                call_btn_view.touch()

            # Ask code
            code = code_callback()

            if code:
                if self._try_code(vc, code):
                    return True

                logger.error('Verification code NOT valid!')
                request_call = False
            else:
                # Request call again
                logger.info('Attempting to request a new Call...')
                request_call = True

    def _try_code(self, vc, code):
        # Input text
        code_input_view = self._wait_views(vc, 'com.whatsapp:id/verify_sms_code_input')

        if not code_input_view:
            raise WaException('Could not find SMS code input TextEdit')

        code_input_view.setText(code)

        # Check if valid
        msg_view = self._wait_views(vc, 'android:id/message')

        if msg_view:
            logger.info('Dialog message: %s', msg_view.getText())

            # Click OK button to close dialog
            ok_btn = self._wait_views(vc, 'android:id/button1')

            if ok_btn:
                ok_btn.touch()

            # If input is blocked, wait
            while True:
                input_blocked_view = self._wait_views(vc, 'com.whatsapp:id/description_2_bottom', max_tries=3)

                if not input_blocked_view:
                    return True

                if input_blocked_view.getText().find('Wait') == -1:
                    break

                logger.info('Waiting for input to unblock...')
                time.sleep(10)

            return False

        return True

    def _get_countdown_sms(self, vc):
        return self._get_countdown(vc, 'com.whatsapp:id/countdown_time_sms')

    def _get_countdown_call(self, vc):
        return self._get_countdown(vc, 'com.whatsapp:id/countdown_time_voice')

    def _get_countdown(self, vc, id):
        sms_count_view = self._wait_views(vc, id)

        if sms_count_view:
            sms_text = sms_count_view.getText()
            sms_text_parts = sms_text.split(':')

            if len(sms_text_parts) != 2:
                raise WaException('Malformed sms countdown text! (%s)' % sms_text)

            return int(sms_text_parts[0]) * 60 + int(sms_text_parts[1])

        raise WaException('Cannot find countdown seconds')

    def _allow_access(self, vc):
        # Continue
        continue_view = self._wait_views(vc, 'com.whatsapp:id/submit')

        if not continue_view:
            return False

        logger.info('Touching "Continue" on permissions box...')

        continue_view.touch()

        # Allow for both photos/media/files and contacts (asked twice)
        for i in range(2):
            allow_view = self._wait_views(vc, 'com.android.packageinstaller:id/permission_allow_button')

            if not allow_view:
                return False

            logger.info('Touch "Allow" (step %d)...', i+1)
            allow_view.touch()

        return True

    def _automate_accept_eula(self, vc):
        msg_view = self._wait_views(vc, 'android:id/message')

        # Accept custom ROM alert
        if msg_view and msg_view.getText().find('ROM') != -1:
            ok_btn = self._wait_views(vc, 'android:id/button2')

            if not ok_btn:
                return False

            logger.info('Touching "OK" at custom ROM alert...')

            ok_btn.touch()

        # Agree to EULA
        msg_view = self._wait_views(vc, 'com.whatsapp:id/eula_accept')

        if not msg_view:
            return False

        logger.info('Agreeing to EULA...')

        msg_view.touch()
        return True

    def _install(self):
        apk_path = os.path.abspath(os.path.join('apks', 'WhatsApp.apk'))

        if self._is_app_installed():
            return True

        return self.adb_client.install(apk_path)

    def _uninstall(self):
        if not self._is_app_installed():
            return True

        return self.adb_client.uninstall("com.whatsapp")

    def _open_app(self):
        return self.adb_client.shell('am start -n com.whatsapp/com.whatsapp.registration.EULA').find('Error') == -1

    def _is_app_installed(self):
        return self.adb_client.is_installed('com.whatsapp')

    def _wait_views(self, vc, ids, frequency=2, max_tries=10):
        ids = ids if isinstance(ids, list) else [ids]
        i = 0

        while i < max_tries:
            # Update view
            try:
                with suppress_stderr():
                    vc.dump(sleep=0)
            except RuntimeError, e:
                logger.error('Exception while trying to dump views: %s', e.message)
                pass

            # Check if it can find any of the IDs
            for id in ids:
                view = vc.findViewById(id)

                if view:
                    if logger.level == logging.DEBUG:
                        vc.traverse()

                    return view

            # Check every X seconds
            time.sleep(frequency)

            i += 1

        return None