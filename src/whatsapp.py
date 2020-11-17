import os
import logging
import time

from adb import InstallError
from src.utils import suppress_stderr

from com.dtmilano.android.viewclient import ViewClient

class WaException:
    def __init__(self, reason):
        self.reason = reason


class WhatsApp:
    def __init__(self, adb_client):
        self.logger = logging.getLogger("{} - WhatsApp".format(self.adb_client.serial))
        self.adb_client = adb_client
        self.device = None
        self.vc = None

    def extract_msgstore(self, dst_path):
        storage_paths = [self.adb_client.shell("echo $EXTERNAL_STORAGE"), "/storage/emulated/0"]

        # get most recent msgstore db path
        for spath in storage_paths:
            db_path = self.adb_client.shell("ls -t %s/Whatsapp/Databases/msgstore* | head -1" % spath.rstrip()).rstrip()

            if not db_path:
                continue

            dst_full_path = os.path.join(dst_path, os.path.basename(db_path))
            self.logger.info("Extracting msgstore database from path: %s", db_path)

            # Returns None on success
            if self.adb_client.pull(db_path, dst_full_path) is None:
                return dst_full_path

        return None

    def extract_priv_key(self, dst_path):
        dst_full_path = os.path.join(dst_path, "key")
        return self.adb_client.pull("/data/data/com.whatsapp/files/key", dst_full_path) is None

    def register_phone(self, msgstore_path, country_code, phone_no):
        # Step 1: cleanup
        if not self._uninstall():
            raise WaException("Can not cleanup device")

        # Step 2: install
        self.logger.info("Installing WhatsApp...")

        try:
            if not self._install():
                raise WaException("Can not install WhatsApp APK")
        except InstallError as e:
            raise WaException(e.message)

        # Step 3a: create / clean /WhatsApp/ data directory
        self.logger.info("Cleaning WhatsApp...")
        self.logger.info(self.adb_client.shell("rm -rf /sdcard/WhatsApp/Databases/*"))

        # Step 3b: move msgstore.db to correct location
        self.logger.info("Moving extracted database into emulator...")
        self.logger.info(self.adb_client.push(msgstore_path, "/sdcard/WhatsApp/Databases/msgstore.db.crypt12"))

        self.connect_device()

        for permission in [
            "android.permission.WRITE_CONTACTS",
            "android.permission.READ_CONTACTS",
            "android.permission.WRITE_EXTERNAL_STORAGE",
            "android.permission.READ_EXTERNAL_STORAGE",
        ]:
            self.logger.info("pm grant com.whatsapp {}".format(permission))
            self.adb_client.shell("pm grant com.whatsapp {}".format(permission))
            time.sleep(0.05)

        if not self._open_app():
            raise WaException("Can not open WhatsApp application")

        time.sleep(15)
        if not self._automate_accept_eula():
            raise WaException("Can not accept EULA")

        if not self._do_verify(country_code, phone_no):
            raise WaException("Can not verify phone number")

    def _do_verify(self, cc, phone):
        # Set country code
        cc_view = self._wait_views("com.whatsapp:id/registration_cc")
        self.logger.info("Touching and changing country code TextEdit...")

        if not cc_view:
            return False

        cc_view.touch()
        cc_view.setText(str(cc))

        # Set phone number
        number_view = self._wait_views("com.whatsapp:id/registration_phone")
        self.logger.info("Touching and changing phone number TextEdit...")
        if not number_view:
            return False

        number_view.touch()
        number_view.setText(str(phone))

        # Click "Next"
        next_view = self._wait_views("com.whatsapp:id/registration_submit")
        self.logger.info("Touching registration submit button...")
        if not next_view:
            return False
        next_view.touch()

        # Confirm Dialog clicking "OK"
        # Extend timeout to be 6*5 seconds (5 minutes) because WhatsApp could take time to send code
        confirm_view = self._wait_views("android:id/button1", max_tries=60, frequency=5)
        self.logger.info("Touching OK confirmation button...")
        if not confirm_view:
            return False
        confirm_view.touch()
        return True

    def connect_device(self, back_home=False):
        if self.device is None:
            # We have founded a strange issue, when the screen appear on wait security code the countdown for another code refresh too frequently
            # Go back to home please ...
            if back_home is True:
                self.adb_client.shell("input keyevent KEYCODE_HOME")
                time.sleep(2)

            kwargs1 = {'serialno': self.adb_client.serial, 'verbose': False, 'ignoresecuredevice': False, 'ignoreversioncheck': False}
            self.device, serialno = ViewClient.connectToDeviceOrExit(**kwargs1)

            kwargs2 = {'forceviewserveruse': False, 'startviewserver': True, 'autodump': False, 'ignoreuiautomatorkilled': True, 'compresseddump': True, 'useuiautomatorhelper': False, 'debug': {}}
            self.vc = ViewClient(self.device, serialno, **kwargs2)

    def _verify_by_sms(self, code_callback):
        self.connect_device()

        while True:
            code = code_callback if type(code_callback) in [str, int] else code_callback()
            if code is not None:
                if self._try_code(code):
                    return True

                self.logger.error("Verification code NOT valid!")
            else:
                # Attempt to re-send SMS
                self.logger.info("Attempting to re-send verification code...")

                try:
                    seconds_to_wait = self._get_countdown_sms()

                    # Got countdown seconds, wait and request new code
                    self.logger.info("Waiting %d seconds to request new SMS", seconds_to_wait)
                    time.sleep(seconds_to_wait)
                except WaException:
                    pass

                # Resend SMS
                resend_view = self._wait_views("com.whatsapp:id/resend_sms_btn")

                if not resend_view:
                    raise WaException("Cannot find resend sms button view")

                # Resend SMS button might be disabled for few hours
                if resend_view.getText().find(" in ") != -1:
                    raise WaException("Cannot request new code, try again later: %s" % resend_view.getText())

                # Touch
                resend_view = self._wait_views("com.whatsapp:id/resend_sms_btn")
                resend_view.touch()

    def _verify_by_call(self, code_callback):
        self.connect_device()

        request_call = True
        time.sleep(1)

        while True:
            call_btn_view = self._wait_views("com.whatsapp:id/call_btn")
            countdown_view = self._wait_views("com.whatsapp:id/countdown_time_voice", max_tries=1)

            if not call_btn_view:
                raise WaException("Could not find call button view")

            if request_call and call_btn_view.getText().find(" in ") != -1:
                raise WaException("Cannot request new code, try again later: %s" % call_btn_view.getText())

            if request_call and countdown_view:
                try:
                    seconds_to_wait = self._get_countdown_call()

                    # Got countdown seconds, wait and request new code
                    self.logger.info("Waiting %d seconds to request a call", seconds_to_wait)
                    time.sleep(seconds_to_wait)
                except WaException:
                    pass

            if request_call:
                # Update & Touch
                resend_view = self._wait_views("com.whatsapp:id/call_btn")
                resend_view.touch()

                call_btn_view = self._wait_views("com.whatsapp:id/call_btn")
                call_btn_view.touch()

            # Ask code
            code = code_callback if type(code_callback) in [str, int] else code_callback()
            if code is not None:
                if self._try_code(code):
                    return True

                self.logger.error("Verification code NOT valid!")
                request_call = False
            else:
                # Request call again
                self.logger.info("Attempting to request a new Call...")
                request_call = True

    def _try_code_adb(self, code):
        self.logger.info("am start -a android.intent.action.VIEW -d https://v.whatsapp.com/{} com.whatsapp".format(code))
        return self.adb_client.shell("am start -a android.intent.action.VIEW -d https://v.whatsapp.com/{} com.whatsapp".format(code)).find("Error") == -1

    def _try_code(self, code, use_adb=True):
        if use_adb is True and self._try_code_adb(code) is True:
            return True  # If failed, continue with ui interaction

        # Input text
        code_input_view = self._wait_views("com.whatsapp:id/verify_sms_code_input")

        if not code_input_view:
            self.logger.error("Could not find SMS code input TextEdit")
            raise WaException("Could not find SMS code input TextEdit")

        code_input_view.setText(code)

        # Check if valid
        msg_view = self._wait_views("android:id/message")

        if msg_view:
            self.logger.info("Dialog message: %s", msg_view.getText())

            # Click OK button to close dialog
            ok_btn = self._wait_views("android:id/button1")

            if ok_btn:
                ok_btn.touch()

            # If input is blocked, wait
            while True:
                input_blocked_view = self._wait_views("com.whatsapp:id/description_2_bottom", max_tries=3)

                if not input_blocked_view:
                    return True

                if input_blocked_view.getText().find("Wait") == -1:
                    break

                self.logger.info("Waiting for input to unblock...")
                time.sleep(10)

            return False

        return True

    def _get_countdown_sms(self):
        return self._get_countdown("com.whatsapp:id/countdown_time_sms")

    def _get_countdown_call(self):
        return self._get_countdown("com.whatsapp:id/countdown_time_voice")

    def _get_countdown(self, id):
        sms_count_view = self._wait_views(id)

        if sms_count_view:
            sms_text = sms_count_view.getText()
            sms_text_parts = sms_text.split(":")

            if len(sms_text_parts) != 2:
                raise WaException("Malformed sms countdown text! (%s)" % sms_text)

            return int(sms_text_parts[0]) * 60 + int(sms_text_parts[1])

        raise WaException("Cannot find countdown seconds")

    def _allow_access(self):
        continue_view = self._wait_views("com.whatsapp:id/submit", frequency=3, max_tries=5)

        if not continue_view:
            return False

        self.logger.info('Touching "Continue" on permissions box...')
        continue_view.touch()

        # Allow for both photos/media/files and contacts (asked twice)
        for i in range(2):
            allow_view = self._wait_views("com.android.packageinstaller:id/permission_allow_button", frequency=3, max_tries=5)
            if not allow_view:
                return False

            self.logger.info('Touch "Allow" (step %d)...', i + 1)
            allow_view.touch()

        return True

    def _automate_accept_eula(self):
        msg_view = self._wait_views("android:id/message", frequency=6, max_tries=5)

        # Accept custom ROM alert
        if msg_view and msg_view.getText().find("ROM") != -1:
            ok_btn = self._wait_views("android:id/button2")

            if not ok_btn:
                return False

            self.logger.info('Touching "OK" at custom ROM alert...')
            ok_btn.touch()

        # Agree to EULA
        msg_view = self._wait_views("com.whatsapp:id/eula_accept")
        if not msg_view:
            return False

        self.logger.info("Agreeing to EULA...")
        msg_view.touch()
        return True

    def _install(self):
        apk_path = os.path.abspath(os.path.join("apks", "WhatsApp.apk"))

        if self._is_app_installed():
            return True

        return self.adb_client.install(apk_path)

    def _uninstall(self):
        if not self._is_app_installed():
            return True
        return self.adb_client.uninstall("com.whatsapp")

    def _open_app(self):
        return self.adb_client.shell("am start -n com.whatsapp/com.whatsapp.registration.EULA").find("Error") == -1

    def _is_app_installed(self):
        return self.adb_client.is_installed("com.whatsapp")

    def _wait_views(self, ids, frequency=2, max_tries=10):
        ids = ids if isinstance(ids, list) else [ids]
        for attempt in range(max_tries):
            try:  # Update view
                with suppress_stderr():
                    items = self.vc.dump(window='-1')
            except RuntimeError as e:
                self.logger.error("{} - Exception while trying to dump views: {}".format(attempt, e))

            # Check if it can find any of the IDs
            for _id in ids:
                view = self.vc.findViewById(_id)
                if view:
                    # if self.logger.level == logging.DEBUG:
                    # self.vc.traverse()
                    return view

            time.sleep(frequency)  # Check every X seconds
        return None

    def complete_registration(self, cc, phone):
        gdrive_msg_view = self._wait_views("com.whatsapp:id/permission_message", max_tries=5)
        skip_btn_view = self._wait_views("com.whatsapp:id/submit", max_tries=5)

        if not gdrive_msg_view and not skip_btn_view:
            self.logger.info("1. Expected Google Drive permission dialog, ignoring..")
        else:
            skip_btn_view.touch()

        # Restore messages
        gdrive_msg_view = self._wait_views("android:id/message", max_tries=5)
        skip_btn_view = self._wait_views("android:id/button2", max_tries=5)

        if not gdrive_msg_view and not skip_btn_view:
            self.logger.info("2. Expected Google Drive permission dialog, ignoring..")
        else:
            skip_btn_view.touch()

        # Restore messages Activity
        restore_btn_view = self._wait_views("com.whatsapp:id/perform_restore", max_tries=30, frequency=5)

        if not restore_btn_view:
            raise WaException("Cannot find restore button, is msgcrypt associated with +%d %s?" % (cc, phone))

        self.logger.info("Restoring messages... (might take a while)")
        restore_btn_view.touch()

        # Wait for result (max 15 minutes)
        result_msg_view = self._wait_views("com.whatsapp:id/msgrestore_result_box", frequency=10, max_tries=90)

        if not result_msg_view:
            raise WaException("Could not restore messages")

        self.logger.info("%s", result_msg_view.getText())
        return True
