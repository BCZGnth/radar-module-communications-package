import serial_protocol as sp
import serial

ld2450 = serial.Serial("COM5", 256000, timeout=1)

send_data = bytearray([])
receive_data = bytearray([])

try:
    while True:
        intra_length =  bytearray(input("\nHow long is this internal command? >> "), encoding="")
        command_word =  bytearray(input("\nWhat is the command word? >> "))
        command_value = bytearray(input("\nWhat is the command value? >> "))

        sp._send_command()

except KeyboardInterrupt:
    print("Serial Port Closed")