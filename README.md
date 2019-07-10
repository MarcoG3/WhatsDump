# WhatsDump
Extract WhatsApp private key from any Android device (Android 7+ supported).
This tool spawns a clean Android 6 emulator and attempts to register with your number to extract msgstore private key.

*NOTE: this tool is in beta stage and might not be stable. You're more than welcome to improve this software by submitting a PR or an issue!*

### SUPPORTED OPERATING SYSTEMS

- Mac OSX
- Windows
- Linux

### RELEASES

To use WhatsDump without installing Python and its dependencies, you can find pre-built binaries (thanks to PyInstaller) here: https://github.com/MarcoG3/WhatsDump/releases

### USE CASE
You want to decrypt and/or extract msgstore.db database from your Android device.
  
  1. Install SDK with --install-sdk flag
  2. Attach Android device to USB port and launch WhatsDump
  3. Wait the script to quickly register your phone number on emulator
  4. Wait for SMS or CALL with confirmation code
  5. Input 6-digit confirmation code
  6. Private key is extracted in output/ directory

### OPTIONS


| Flag            |               | Behaviour     |
| -------------   | ------------- | ------------- |
| --wa-phone      | Required      | WhatsApp phone number associated with msgstore database <br />from which you will receive verification SMS (with prefix, ex. +393387182291  |
| --wa-verify     | Required      | Phone verification method to use (SMS or CALL)  |
| --install-sdk   | Optional      | Installs Android SDK on android-sdk/ directory. This is mandatory to run WhatsDump  |
| --msgstore     | Optional      | Location of msgstore database to decrypt (or plug in device to USB port)  |
| --verbose       | Optional      | Show verbose (debug) output  |
| --show-emulator | Optional      | Show emulator screen (by default headless)  |
| --no-accel      | Optional      | Disable hardware acceleration (very slow emulator)  |


### EXAMPLES

##### PLUGGED IN PHONE
```python whatsdump.py --wa-phone +15417543010 --wa-verify sms```

##### EXTERNAL MSGSTORE.DB
```python whatsdump.py --msgstore /path/to/msgstore.db --wa-phone +15417543010 --wa-verify sms```

### PREREQUISITES

  - Java JDK must be installed (JAVA_HOME environment variable must be set)
  - Hardware acceleration must be enabled to run Emulator without issues
  - SIM card associated with msgstore.db to receive WhatsApp confirmation PIN (SMS or CALL)
  
  - Install all the Python library dependencies by running the following command: `pip install -r requirements.txt`
  
### THIRD-PARTY LIBRARIES USED

  - [AndroidViewClient](https://github.com/dtmilano/AndroidViewClient/) by dtmilano
  - [pure-python-adb](https://github.com/Swind/pure-python-adb) by Swind 
