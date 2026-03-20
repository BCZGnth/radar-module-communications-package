import serial_protocol

import serial
import os
import json

# Open the serial port
ser = serial.Serial('COM5', 256000, timeout=1)
results = []

FOLDER = "tests"
PREFIX = "ld2450_test_"
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

try:
    while True:
        # Read a line from the serial port
        serial_port_line = ser.read_until(serial_protocol.REPORT_TAIL)

        all_target_values = serial_protocol.read_radar_data(serial_port_line)
        
        if all_target_values is None:
            continue

        target1_x, target1_y, target1_speed, target1_distance_res, \
        target2_x, target2_y, target2_speed, target2_distance_res, \
        target3_x, target3_y, target3_speed, target3_distance_res \
            = all_target_values

        # clear the screen to keep the data in the same spot
        os.system('cls')
        
        # Print the interpreted information for all targets
        print(f'Target 1 x-coordinate: {target1_x} mm')
        print(f'Target 1 y-coordinate: {target1_y} mm')
        print(f'Target 1 speed: {target1_speed} cm/s')
        print(f'Target 1 distance res: {target1_distance_res} mm')

        print(f'Target 2 x-coordinate: {target2_x} mm')
        print(f'Target 2 y-coordinate: {target2_y} mm')
        print(f'Target 2 speed: {target2_speed} cm/s')
        print(f'Target 2 distance res: {target2_distance_res} mm')

        print(f'Target 3 x-coordinate: {target3_x} mm')
        print(f'Target 3 y-coordinate: {target3_y} mm')
        print(f'Target 3 speed: {target3_speed} cm/s')
        print(f'Target 3 distance res: {target3_distance_res} mm')

        print('-' * 30)

        results.append(all_target_values)


except KeyboardInterrupt:
    # Close the serial port on keyboard interrupt
    ser.close()

    with open(filepath, "w") as f:
        json.dump(results, f)

    print("Serial port closed.")