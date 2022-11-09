#!/usr/bin/env python

"""
A class to put a simple service on the dbus, according to victron standards, with constantly updating
paths. See example usage below. It is used to generate dummy data for other processes that rely on the
dbus. See files in dbus_vebus_to_pvinverter/test and dbus_vrm/test for other usage examples.

To change a value while testing, without stopping your dummy script and changing its initial value, write
to the dummy data via the dbus. See example.

https://github.com/victronenergy/dbus_vebus_to_pvinverter/tree/master/test
"""
from gi.repository import GLib
import dbus
import platform
import argparse
import logging
import sys
import os
import requests # for http GET

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '../ext/velib_python'))
from vedbus import VeDbusService, VeDbusItemImport, VeDbusItemExport

class DbusDummyService:
    def __init__(self, servicename, deviceinstance, paths, productname='CVL Optimizer', connection='Python'):
        self._dbusservice = VeDbusService(servicename)
        self._paths = paths

        dbusConn = dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
        self._batteryService = VeDbusItemImport(dbusConn, 'com.victronenergy.system', '/Dc/Battery/BatteryService').get_value()

        self._MAX_CELL_VOLTAGE = 3.45
        self._MIN_CELL_VOLTAGE = 2.9
        self._MAX_VOLTAGE_DIFF = 0.01 # Max precision for MPPTs are 10mV

        logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

        # Create the management objects, as specified in the ccgx dbus-api document
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
        self._dbusservice.add_path('/Mgmt/Connection', connection)

        # Create the mandatory objects
        self._dbusservice.add_path('/DeviceInstance', deviceinstance)
        # self._dbusservice.add_path('/ProductId', 20)
        self._dbusservice.add_path('/ProductName', productname)
        self._dbusservice.add_path('/FirmwareVersion', 0)
        self._dbusservice.add_path('/HardwareVersion', 0)
        self._dbusservice.add_path('/Connected', 1)
        self._dbusservice.add_path('/ErrorCode', 0)

        _kwh = lambda p, v: (str(v) + 'kWh')
        _a = lambda p, v: (str(v) + 'A')
        _ah = lambda p, v: (str(v) + 'Ah')
        _w = lambda p, v: (str(v) + 'W')
        _v = lambda p, v: (str(v) + 'V')
        _p = lambda p, v: (str(v) + '%')
        _c = lambda p, v: (str(v) + '°C')

        service = self._dbusservice
        service.add_path('/Soc', None, gettextcallback=_p)
        service.add_path('/Dc/0/Voltage', None, gettextcallback=_v)

        service.add_path('/Io/AllowToCharge', None)
        service.add_path('/Io/AllowToDischarge', None)
        service.add_path('/Info/MaxChargeVoltage', None, gettextcallback=_v)
        service.add_path('/System/MaxCellVoltage', None, gettextcallback=_v)
        service.add_path('/System/MinCellVoltage', None, gettextcallback=_v)
        service.add_path('/Voltages/Diff', None, gettextcallback=_v)
        service.add_path('/System/NrOfCellsPerBattery', None)

        for path, settings in self._paths.items():
            self._dbusservice.add_path(
                path, settings['initial'], writeable=True, onchangecallback=self._handlechangedvalue)

        GLib.timeout_add(1000, self._update)

    def _update(self):

        try:
            #logging.info(self._dbusservice['/InstalledCapacity'])

            dbusConn = dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
            #logging.info(dbusConn.list_names())

            self._dbusservice['/Info/MaxChargeVoltage'] = VeDbusItemImport(dbusConn, self._batteryService, '/Info/MaxChargeVoltage').get_value()
            self._dbusservice['/System/MaxCellVoltage'] = VeDbusItemImport(dbusConn, self._batteryService, '/System/MaxCellVoltage').get_value()
            self._dbusservice['/System/MinCellVoltage'] = VeDbusItemImport(dbusConn, self._batteryService, '/System/MinCellVoltage').get_value()
            self._dbusservice['/Voltages/Diff'] = VeDbusItemImport(dbusConn, self._batteryService, '/Voltages/Diff').get_value()
            self._dbusservice['/Soc'] = VeDbusItemImport(dbusConn, self._batteryService, '/Soc').get_value()
            self._dbusservice['/Io/AllowToCharge'] = VeDbusItemImport(dbusConn, self._batteryService, '/Io/AllowToCharge').get_value()
            self._dbusservice['/Io/AllowToDischarge'] = VeDbusItemImport(dbusConn, self._batteryService, '/Io/AllowToDischarge').get_value()
            self._dbusservice['/Dc/0/Voltage'] = VeDbusItemImport(dbusConn, self._batteryService, '/Dc/0/Voltage').get_value()
            self._dbusservice['/System/NrOfCellsPerBattery'] = VeDbusItemImport(dbusConn, self._batteryService, '/System/NrOfCellsPerBattery').get_value()

            self._mbv = self._MAX_CELL_VOLTAGE * self._dbusservice['/System/NrOfCellsPerBattery']

            if self._dbusservice['/System/MaxCellVoltage'] <= self._MAX_CELL_VOLTAGE:
                cvl = self._mbv + self._MAX_VOLTAGE_DIFF # not there yet, charge with slightly higher voltage to get that current flowing

            if self._dbusservice['/System/MaxCellVoltage'] > self._MAX_CELL_VOLTAGE:
                if self._dbusservice['/System/MinCellVoltage'] < self._MAX_CELL_VOLTAGE:
                    cvl = self._dbusservice['/Dc/0/Voltage'] + self._MAX_VOLTAGE_DIFF # balancing, highest cell can't exceed mcv+x, lower cells can catch up
                else:                    
                    cvl = self._mbv # all cells at 100% SoC (for given maxcv), balancer has to bleed x from cells above maxcv

            if self._dbusservice['/System/MaxCellVoltage'] > self._MAX_CELL_VOLTAGE + self._MAX_VOLTAGE_DIFF:
                cvl = self._dbusservice['/Dc/0/Voltage'] # just in case - balancer could not cope with current, pause charging

            logging.info(round(cvl, 2))
            VeDbusItemImport(dbusConn, 'com.victronenergy.settings', '/Settings/SystemSetup/MaxChargeVoltage').set_value(round(cvl, 2))
            #Settings/SystemSetup/MaxChargeVoltage

        except:
            logging.info('Cant read from dbus')

        return True

    def _handlechangedvalue(self, path, value):
        logging.info("someone else updated %s to %s" % (path, value))
        return True # accept the change


# === All code below is to simply run it from the commandline for debugging purposes ===

# It will created a dbus service called com.victronenergy.pvinverter.output.
# To try this on commandline, start this program in one terminal, and try these commands
# from another terminal:
# dbus com.victronenergy.pvinverter.output
# dbus com.victronenergy.pvinverter.output /Ac/Energy/Forward GetValue
# dbus com.victronenergy.pvinverter.output /Ac/Energy/Forward SetValue %20
#
# Above examples use this dbus client: http://code.google.com/p/dbus-tools/wiki/DBusCli
# See their manual to explain the % in %20

def main():
    # logging.basicConfig(level=logging.DEBUG)
    logging.basicConfig(level=logging.INFO)

    from dbus.mainloop.glib import DBusGMainLoop
    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    pvac_output = DbusDummyService(
        servicename='com.victronenergy.optimized-cvl',
        deviceinstance=21, #pvinverters from 20-29
        paths={
	    #'/InstalledCapacity': {'initial': 460},
        })

    logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()


