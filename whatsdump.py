# -*- coding: utf-8 -*-

import argparse
import sys
import phonenumbers
import os
import logging
import re

from src.utils import sha256
from src.android_sdk import AndroidSDK
from src.whatsapp import WhatsApp, WaException
from adb.client import Client as AdbClient
from phonenumbers.phonenumberutil import NumberParseException

logger = logging.getLogger('WhatsDump')


def wa_code_callback():
    code = ''

    while len(code) != 6:
        code = raw_input('\n>> 6-Digit Verification Code (empty string to resend): ')
        code = code.strip()
        code = re.sub(r'-\s*', '', code)

        # empty -> resend
        if code == '':
            return None

    return code


def main():
    phone = None
    source_device = None
    sdk = AndroidSDK()
    parser = argparse.ArgumentParser(prog='WhatsDump')

    parser.add_argument('--install-sdk', action='store_true', help='Download & extract latest Android SDK emulator packages')
    parser.add_argument('--msgstore', help='Location of msgstore database to decrypt')
    parser.add_argument('--wa-phone', help='WhatsApp phone number associated with msgstore database from which '
                                           'you will receive verification SMS (with prefix, ex. +393387182291)')
    parser.add_argument('--wa-verify', choices=['sms', 'call'], help='Phone verification method to use')
    parser.add_argument('--verbose', action='store_true', help='Show verbose (debug) output')
    parser.add_argument('--show-emulator', action='store_true', help='Show emulator screen (by default headless)')
    parser.add_argument('--no-accel', action='store_true', help='Disable hardware acceleration (very slow emulator!)')

    args = parser.parse_args()

    print('''
 _    _ _           _      ______                       
| |  | | |         | |     |  _  \                      
| |  | | |__   __ _| |_ ___| | | |_   _ _ __ ___  _ __  
| |/\| | '_ \ / _` | __/ __| | | | | | | '_ ` _ \| '_ \ 
\  /\  / | | | (_| | |_\__ \ |/ /| |_| | | | | | | |_) |
 \/  \/|_| |_|\__,_|\__|___/___/  \__,_|_| |_| |_| .__/ 
                                                 | |    
                                                 |_|    
                        v0.2 beta
    ''')

    # Setup logging
    logging.basicConfig(format='[%(levelname)s] %(message)s', stream=sys.stdout)
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    # TODO: CHECK IF JAVA IS INSTALLED

    # SDK Checks
    is_avd_installed = sdk.is_avd_installed()

    if args.install_sdk:
        if is_avd_installed:
            logger.error("WhatsDump AVD already installed! Remove android-sdk/ directory to reinstall Android SDK")
            sys.exit(1)

        # download&install
        if not sdk.install():
            logger.error('Failed to install Android SDK')
            sys.exit(1)

        logger.info('\nAndroid AVD successfully installed')
        sys.exit(0)
    else:
        if not is_avd_installed:
            logger.error("Cannot find WhatsDump AVD; install Android SDK and emulator packages with --install-sdk")
            sys.exit(1)

    # Connect / Start ADB server
    adb_client = AdbClient()

    try:
        logger.info("Connected to ADB (version %d) @ 127.0.0.1:5037" % adb_client.version())
    except:
        logger.info("Attempting to start ADB server...")

        if sdk.start_adb():
            logger.info("ADB server started successfully")
            adb_client = AdbClient()

            logger.info("Connected to ADB (version %d) @ 127.0.0.1:5037" % adb_client.version())
        else:
            logger.error('Could not connect/start ADB server')
            sys.exit(1)

    # Require msgstore or connected device
    if args.msgstore:
        # Check if file exists
        if not os.path.isfile(args.msgstore):
            logger.error("Msgstore location is not valid (file does not exist)")
            sys.exit(1)
    else:
        logger.info("Msgstore location not provided, attempting to find connected devices with ADB...\n")

        devices = adb_client.devices()
        i = 0

        # If no devices and no msgstore, quit
        if len(devices) == 0:
            logger.error("Cannot find any connected devices")
            sys.exit(1)

        # Show all devices
        for device in devices:
            print("\t[%d] %s (%s)" % (i, device.serial, device.shell('getprop ro.product.name').rstrip()))
            i += 1

        print('\n')
        while source_device is None:
            dev_index = int(raw_input("\n>> Which device number you want to extract msgstore from?: "))

            if dev_index < 0 or dev_index+1 > len(devices):
                continue

            source_device = devices[dev_index]
            print('\n')

    # Validate required phone
    if not args.wa_phone:
        logger.error("Please provide the phone number associated with msgstore")
        sys.exit(1)
    else:
        # Add "+" if not given
        if args.wa_phone[0] != '+':
            args.wa_phone = '+' + args.wa_phone

        try:
            phone = phonenumbers.parse(args.wa_phone)
        except NumberParseException:
            pass

        if not phone:
            logger.error("Provided phone number is NOT valid")
            sys.exit(1)

    if not args.wa_verify:
        logger.error("Please provide a WhatsApp verification method")
        sys.exit(1)

    # recap
    if source_device:
        logger.info('Extract WhatsApp database from device >> %s', source_device.serial)
    else:
        logger.info('Using msgstore database from path: %s', args.msgstore)

    logger.info('Using WhatsApp phone number: +%d %d', phone.country_code, phone.national_number)
    logger.info('Using WhatsApp verification method: %s', args.wa_verify.upper())

    yn = raw_input("\n>> Continue? (y/n): ")

    if yn != 'y':
        sys.exit(0)

    # create phone directory tree where to store results
    dst_path = os.path.join(os.path.abspath('output'), str(phone.national_number))
    if not os.path.exists(dst_path):
        try:
            os.makedirs(dst_path)
        except OSError:
            logging.error('Cannot create output directory tree')
            sys.exit(1)

    log_formatter = logging.Formatter("%(asctime)s - [%(levelname)s]: %(message)s")
    file_handler = logging.FileHandler(os.path.join(dst_path, 'log.txt'))
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

    # Extract msgstore.db from source device, if any
    msgstore_path = args.msgstore

    if msgstore_path:
        logger.info('Provided msgstore.db SHA-256 hash: %s', sha256(args.msgstore))

    if source_device:
        logger.info('Extracting msgstore.db.crypt from phone to output/%ld/ ...' % phone.national_number)

        wa = WhatsApp(source_device)
        msgstore_path = wa.extract_msgstore(dst_path)

        if not msgstore_path:
            logger.error('Could not find/extract msgstore database from device (is WhatsApp installed?)')
            sys.exit(1)

        logger.info('Extracted msgstore.db SHA-256 hash: %s', sha256(msgstore_path))

    # Start emulator and connect to it
    logger.info('Starting emulator...')

    if args.no_accel:
        logger.warn('Hardware acceleration disabled! Device might be very slow')

    emulator_device = sdk.start_emulator(adb_client, args.show_emulator, args.no_accel)

    if not emulator_device:
        logger.error('Could not start emulator!')
        sys.exit(1)

    if args.show_emulator:
        logger.info('Do not interact with the emulator!')

    logger.info('Trying to register phone on emulator... (may take few minutes)')

    # Attempt to register phone using provided msgstore
    wa_emu = WhatsApp(emulator_device)

    try:
        wa_emu.register_phone(msgstore_path, phone.country_code, phone.national_number, args.wa_verify, wa_code_callback)
    except WaException, e:
        logger.error('Exception in verification: %s', e.reason)
        sys.exit(1)

    logger.info('Phone registered successfully!')
    logger.info('Extracting key...')

    # Extract private key
    if not wa_emu.extract_priv_key(dst_path):
        logger.error('Could not extract private key!')
        sys.exit(1)

    logger.info('Private key extracted in %s', os.path.join(dst_path, 'key'))


if __name__ == '__main__':
    main()