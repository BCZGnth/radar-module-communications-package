# LD2410C mmWave human presence sensor
This sensor uses 24GHz mmWave technology to detect humans at rest or in motion. This project includes a python driver to interact with the sensor through the UART serial port. The driver is written primarily based on the serial communications reference pdf that is included here.

# Usage
To connect with the sensor you would likely need a usb-to-serial convertor or something like a raspberry pi with GPIO to connect to the UART pins of the device.
At the bottom of the ld2410.py file I included some example configuration and usage of the class. The result of that sample code is the output state.json here which gives a sense of the type of data the sensor can provide.

# Future plans
I developed this with the intention to create some occupancy detection machine learning program. The idea would be to put a few of these in a room and use the different gate energy levels to map out the number of people in the room. This could be a privacy preserving and cost effective means of occupancy detection that would be useful to reduce HVAC costs in many facilities.
