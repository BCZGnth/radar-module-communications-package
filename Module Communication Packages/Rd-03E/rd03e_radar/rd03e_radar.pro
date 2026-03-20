QT += core widgets serialport charts

CONFIG += c++17

TARGET = rd03e_radar
TEMPLATE = app

SOURCES += \
    main.cpp \
    mainwindow.cpp \
    rd03e_driver.cpp

HEADERS += \
    mainwindow.h \
    rd03e_driver.h

# Suppress deprecated warnings
DEFINES += QT_DEPRECATED_WARNINGS
