cmake_minimum_required(VERSION 3.16)
project(rd03e_radar VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

# Find Qt6
find_package(Qt6 REQUIRED COMPONENTS
    Core
    Widgets
    SerialPort
    Charts
)

qt_add_executable(rd03e_radar
    main.cpp
    mainwindow.cpp
    mainwindow.h
    rd03e_driver.cpp
    rd03e_driver.h
)

target_link_libraries(rd03e_radar PRIVATE
    Qt6::Core
    Qt6::Widgets
    Qt6::SerialPort
    Qt6::Charts
)

# Windows: prevent console window in release builds
if(WIN32)
    set_target_properties(rd03e_radar PROPERTIES
        WIN32_EXECUTABLE TRUE
    )
endif()

# Installation
install(TARGETS rd03e_radar
    RUNTIME DESTINATION bin
)
