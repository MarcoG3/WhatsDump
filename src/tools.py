import os
import time

from com.dtmilano.android.viewclient import ViewClient
from com.dtmilano.android.adb.adbclient import AdbClient as VcAdbClient


class ViewClientTools:
    def __init__(self, adb_client):
        self.adb_client = adb_client

    def get_viewclient(self):
        vc_adb = VcAdbClient(self.adb_client.serial, ignoreversioncheck=True)

        #FIXME
        time.sleep(5)

        return ViewClient(device=vc_adb, serialno=self.adb_client.serial, useuiautomatorhelper=True)

    def install_culebra_tools(self):
        tester_path = os.path.abspath(os.path.join('apks', 'culebratester.apk'))
        inst_path = os.path.abspath(os.path.join('apks', 'culebratester.test.apk'))

        if not self.adb_client.is_installed('com.dtmilano.android.culebratester'):
            self.adb_client.install(tester_path)

        if not self.adb_client.is_installed('com.dtmilano.android.culebratester.test'):
            self.adb_client.install(inst_path)