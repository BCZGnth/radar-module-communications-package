from serial import Serial
'''
Written by Justin Albrethsen
This class enables interaction with the LD2410C mmWave human presence sensor
'''

class ld2410:
    def __init__(self,port='COM5',baudrate=256000,timeout=5):
        '''
        Initialize variables, we heavily reference the LD2410C serial communications document 
        '''
        self.ld2410 = Serial(port=port,baudrate=baudrate,timeout=timeout)
        self.data_head = b'\xf4\xf3\xf2\xf1'
        self.data_eof = b'\xF8\xF7\xF6\xF5'
        self.head = b'\xfd\xfc\xfb\xfa'
        self.eof = b'\x04\x03\x02\x01'
        self.config = {}
        self.eng_mode = 0
        self.passwd = b'\x48\x69\x4c\x69\x6e\x6b'
        self.baudrates = {9600:1,19200:2,38400:3,57600:4,115200:5,230400:6,256000:7,460800:8}
    
    def send_cmd(self,cmd):
        '''
        Send command through serial, we prepend the header first and wait until device is waiting
        '''
        cmd_len = len(cmd).to_bytes(2,'little')
        msg = bytearray()
        msg.extend(self.head)
        msg.extend(cmd_len)
        msg.extend(cmd)
        msg.extend(self.eof)
        waiting = self.ld2410.in_waiting
        discard = self.ld2410.read(waiting)
        #print(discard)
        self.ld2410.write(msg)
        return self.parse_resp(self.ld2410.read_until(self.eof))
    
    def parse_resp(self,cmd):
        '''
        Parse the response between the head byte and the eof byte
        '''
        try:
            cmd = cmd.split(self.head)[1]
            cmd = cmd.split(self.eof)[0]
        except IndexError:
            print("bad data cannot parse")
            return b'\x00'
        cmd_len = int.from_bytes(cmd[0:2],"little")
        cmd = cmd[2:]
        if len(cmd) != cmd_len:
            print("wrong length")
        return cmd

    def enable_config(self):
        '''
        Send the byte sequence that enables configuration of the device
        '''
        enable_cmd = b'\xFF\x00\x01\x00'
        resp = self.send_cmd(enable_cmd)
        if resp != b'\xFF\x01\x00\x00\x01\x00\x40\x00':
            print("command failed")
            return 0
        else:
            return 1
    
    def stop_config(self):
        '''
        Send the byte sequence that disables configuration of the device
        '''
        disable_cmd = b'\xFE\x00'
        resp = self.send_cmd(disable_cmd)
        if resp != b'\xFE\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1

    def set_max_distance_duration(self):
        '''
        Read from distance and duration from our config and send command to device.
        Distance is in meters and duration is in seconds
        '''
        m_d = self.config["move_dist_gate"].to_bytes(4,'little')
        s_d = self.config["static_dist_gate"].to_bytes(4,'little')
        n_d = self.config["noone_duration"].to_bytes(4,'little')
        set_mdd_cmd = b'\x60\x00' + b'\x00\x00' + m_d + b'\x01\x00' + s_d + b'\x02\x00' + n_d
        resp = self.send_cmd(set_mdd_cmd)
        if resp != b'\x60\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1

    def read_config(self):
        '''
        Read device configuration from the device and record in our config dictionary
        '''
        read_config_cmd = b'\x61\x00'
        resp = self.send_cmd(read_config_cmd)
        self.config["max_dist_gate"] = resp[5]
        self.config["move_dist_gate"] = resp[6]
        self.config["static_dist_gate"] = resp[7]
        for i in range(9):
            self.config["move_sens_gate_{}".format(i)] = resp[8+i]
        for i in range(9):
            self.config["static_sens_gate_{}".format(i)] = resp[17+i]
        self.config["noone_duration"] = int.from_bytes(resp[-2:],"little")

    def start_eng_mode(self):
        '''
        Engineering mode gives access to more detailed measurement data, enable it here.
        '''
        disable_cmd = b'\x62\x00'
        resp = self.send_cmd(disable_cmd)
        if resp != b'\x62\x01\x00\x00':
            print("command failed")
            return 0
        else:
            self.eng_mode = 1
            return 1
    
    def stop_eng_mode(self):
        '''
        Send byte sequence to disable engineering mode
        '''
        disable_cmd = b'\x63\x00'
        resp = self.send_cmd(disable_cmd)
        if resp != b'\x63\x01\x00\x00':
            print("command failed")
            return 0
        else:
            self.eng_mode = 0
            return 1

    def set_gate_sens(self,_all=False,motion=20,station=25):
        '''
        Set sensitivity level for bodies in motion and also for stationary bodies.
        We can set a universal sensitivity or set values based on our configuration dictionary
        '''
        if _all:
            cmd = b'\x64\x00' + b'\x00\x00' + b'\xFF\xFF\x00\x00'
            cmd += b'\x01\x00' + motion.to_bytes(4,'little')
            cmd += b'\x02\x00' + station.to_bytes(4,'little')
            #print(cmd)
            resp = self.send_cmd(cmd)
            if resp != b'\x64\x01\x00\x00':
                print("command failed")
                return 0
            else:
                return 1
        for i in range(9):
            cmd = b'\x64\x00' + b'\x00\x00' + i.to_bytes(4,"little")
            cmd += b'\x01\x00' + self.config["move_sens_gate_{}".format(i)].to_bytes(4,'little')
            cmd += b'\x02\x00' + self.config["static_sens_gate_{}".format(i)].to_bytes(4,'little')
            #print(cmd)
            resp = self.send_cmd(cmd)
            if resp != b'\x64\x01\x00\x00':
                print("command failed")
                return 0
            else:
                pass
        return 1

    def get_firm_version(self):
        '''
        Retrieve firmware version from the device
        '''
        cmd = b"\xA0\x00"
        resp = self.send_cmd(cmd)
        if b'\xa0\x01\x00\x00\x00\x01' not in resp:
            print("command failed")
            return 0
        else:
            resp = resp.split(b'\xa0\x01\x00\x00\x00\x01')[1]
            version = "V{}.{}.{}{}{}{}".format(resp[1],resp[0],resp[5],resp[4],resp[3],resp[2])
            return version

    def set_baudrate(self,baud):
        '''
        Set baudrate of the device from one of the available rates we defined on init.
        '''
        if baud not in self.baudrates.keys():
            print("bad baudrate")
            return 0
        cmd = b"\xA1\x00"
        cmd += self.baudrates[baud].to_bytes(2,'little')
        resp = self.send_cmd(cmd)
        if resp != b'\xa1\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1
    
    def factory_reset(self):
        '''
        Factory reset the device
        '''
        cmd = b"\xA2\x00"
        resp = self.send_cmd(cmd)
        if resp != b'\xa2\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1

    def restart(self):
        '''
        Restart the device
        '''
        cmd = b"\xA3\x00"
        resp = self.send_cmd(cmd)
        if resp != b'\xa3\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1
    
    def bluetooth_on(self):
        '''
        Enable bluetooth communications from the chip
        '''
        cmd = b"\xA4\x00\x01\x00"
        resp = self.send_cmd(cmd)
        if resp != b'\xa4\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1
     
    def bluetooth_off(self):
        '''
        Send the byte sequence to disable bluetooth on the device
        '''
        cmd = b"\xA4\x00\x00\x00"
        resp = self.send_cmd(cmd)
        if resp != b'\xa4\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1
    
    def bluetooth_mac(self):
        '''
        Read the bluetooth mac address of the device
        '''
        cmd = b"\xA5\x00\x01\x00"
        resp = self.send_cmd(cmd)
        if b'\xa5\x01\x00\x00' not in resp:
            print("command failed")
            return 0
        else:
            mac = resp.split(b'\xa5\x01\x00\x00')[1]
            return "{:02X}:{:02X}:{:02X}:{:02X}:{:02X}:{:02X}".format(mac[0],mac[1],mac[2],mac[3],mac[4],mac[5])
    
    def bluetooth_permissions(self):
        '''
        Use to get bluetooth permissions of the device
        '''
        cmd = b"\xA8\x00"
        cmd += self.passwd
        resp = self.send_cmd(cmd)
        print(resp)
        if resp != b'\xa8\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1

    def set_bt_passwd(self,passwd):
        '''
        Set the bluetooth password, default is "HiLink"
        '''
        cmd = b"\xA9\x00"
        if len(passwd) != 6:
            print("password should be 6 bytes")
            return 0
        cmd += bytes(passwd.encode('utf8'))
        resp = self.send_cmd(cmd)
        if resp != b'\xa9\x01\x00\x00':
            print("command failed")
            return 0
        else:
            self.passwd = bytes(passwd.encode('utf8'))
            return 1
            
    def set_high_res(self):
        '''
        Set resolution to 0.2m
        '''
        cmd = b"\xAA\x00\x01\x00"
        resp = self.send_cmd(cmd)
        if resp != b'\xaa\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1

    def set_low_res(self):
        '''
        Set resolution to 0.75m
        '''
        cmd = b"\xAA\x00\x00\x00"
        resp = self.send_cmd(cmd)
        if resp != b'\xaa\x01\x00\x00':
            print("command failed")
            return 0
        else:
            return 1

    def get_res(self):
        '''
        Get current resolution
        '''
        cmd = b"\xAB\x00"
        resp = self.send_cmd(cmd)
        if resp == b'\xab\x01\x00\x00\x01\x00':
            return 0.2
        elif resp == b'\xab\x01\x00\x00\x00\x00':
            return 0.75
        else:
            print("command failed")
            return 0

    def parse_basic(self,data):
        '''
        Parse the basic reading data and return as dictionary
        '''
        _data = {}
        targets = {0:"None",1:"Moving",2:"Static",3:"Both"}
        _data["target"] = targets[data[0]]
        _data["movement_dist"] = int.from_bytes(data[1:3],'little')
        _data["movement_energy"] = data[3]
        _data["stationary_dist"] = int.from_bytes(data[4:6],'little')
        _data["stationary_energy"] = data[6]
        _data["detect_distance"] = int.from_bytes(data[7:9],'little')
        return _data
    
    def parse_engineer(self,data):
        '''
        Parse detailed engineering mode readings and return as dictionary
        The device has 9 gates, we return their individual energy levels (can detect multi targets)
        '''
        _data = {}
        targets = {0:"None",1:"Moving",2:"Static",3:"Both"}
        _data["target"] = targets[data[0]]
        _data["move_dist"] = int.from_bytes(data[1:3],'little')
        _data["move_energy"] = data[3]
        _data["static_dist"] = int.from_bytes(data[4:6],'little')
        _data["static_energy"] = data[6]
        _data["detect_distance"] = int.from_bytes(data[7:9],'little')
        _data["max_move_dist"] = data[9]
        _data["max_stationary_dist"] = data[10]
        for i in range(9):
            _data["move_gate_{}_energy".format(i)] = data[11+i]
        for i in range(9):
            _data["stationary_gate_{}_energy".format(i)] = data[20+i]
        _data["retain_data"] = str(data[29:])
        return _data
    
    def parse_data(self,cmd):
        '''
        Parse the current reading, first determine if engineering or basic mode and call appropriate parser
        '''
        try:
            cmd = cmd.split(self.data_head)[1]
            cmd = cmd.split(self.data_eof)[0]
        except IndexError:
            print("bad data cannot parse")
            return b'\x00'
        cmd_len = int.from_bytes(cmd[0:2],"little")
        cmd = cmd[2:]
        if len(cmd) != cmd_len:
            print("wrong length")
            return 0
        if cmd[0:2] == b'\x02\xAA' and cmd[-2:] == b'\x55\x00':
            return self.parse_basic(cmd[2:-2])
        elif cmd[0:2] == b'\x01\xAA' and cmd[-2:] == b'\x55\x00':
            return self.parse_engineer(cmd[2:-2])
        else:
            print("bad data type")
            return 0
    
    def read_data(self):
        '''
        Get the current reading
        '''
        return self.parse_data(self.ld2410.read_until(self.data_eof))
        

    def close(self):
        '''
        Close the serial port for the device
        '''
        self.ld2410.close()

if __name__ == "__main__":
    ld = ld2410()

    '''
    Pick a filename for the test:
    '''
    import os

    FOLDER = "tests"
    PREFIX = "ld2410c_test_"
    EXTENSION = ".json"

    os.makedirs(FOLDER, exist_ok=True)

    existing = os.listdir(FOLDER)
    numbers = []
    for name in existing:
        if name.startswith(PREFIX) and name.endswith(EXTENSION):
            middle = name[len(PREFIX):-len(EXTENSION)]
            if middle.isdigit():
                numbers.append(int(middle))

    next_number = max(numbers, default=0) + 1
    filename = f"{PREFIX}{next_number:03d}{EXTENSION}"
    filepath = os.path.join(FOLDER, filename)
    
    # open(filepath, "w").close()
    # print(f"Created: {filepath}")


    '''
    Show some example configuration usage below
    '''
    if ld.enable_config():
        ld.config["version"] = ld.get_firm_version()
        #print(ld.set_baudrate(9600))
        #ld.factory_reset()
        #ld.restart()
        #ld.bluetooth_on()
        ld.config["bt_mac"] = ld.bluetooth_mac()
        ld.set_high_res()
        ld.config["resolution"] = ld.get_res()
        #ld.set_low_res()
        #print(ld.get_res())
        #ld.set_bt_passwd("justin")
        #ld.restart()
        ld.start_eng_mode()
        ld.read_config()
        #print(ld.config)
        #ld.config["noone_duration"] = 6
        #ld.set_max_distance_duration()
        #ld.config["move_sens_gate_2"] = 22
        #ld.set_gate_sens(_all=False)
        #ld.read_config()
        #print(ld.config["move_sens_gate_2"])
        #ld.sVtop_eng_mode()
        ld.stop_config()
    '''
    Take periodic readings every 10 ms and record to json file
    '''
    # import time
    # start = time.time()
    states = {}
    states["config"] = ld.config
    import os
    import json
    import keyboard

    results = []

    # while time.time() < start + 1000:
    while True:
        cur_read = ld.read_data()
        os.system('cls')
        print(f"Target Status: {cur_read['target']}")
        print(f"Target Move Distance: {cur_read['move_dist']}")
        print(f"Target Move Energy: {cur_read['move_energy']}")
        print(f"Target Static Distance: {cur_read['static_dist']}")
        print(f"Target Move Distance: {cur_read['static_energy']}")
        print(f"Detect Distance: {cur_read['detect_distance']}")
        print(f"Max Move Distance{cur_read['max_move_dist']}")
        print(f"Max Stationary Distance{cur_read['max_stationary_dist']}")
        for i in range(9):
            print(f"Move Gate {i} Energy: {cur_read[f'move_gate_{i}_energy']}")

        for i in range(9):
            print(f"Stationary Gate {i} Energy: {cur_read[f'stationary_gate_{i}_energy']}")

        results.append(cur_read)
    
        if keyboard.is_pressed("q"):
            print("Stopping Loop")
            break

    with open(filepath, "w") as f:
        json.dump(results, f, indent=4)

    ld.close()
    # import json
    # with open('states.json','w') as outfile:
    #     json.dump(states,outfile, indent=4)

