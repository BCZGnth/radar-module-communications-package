#include "rd03e_driver.h"
#include <QDateTime>

RD03EDriver::RD03EDriver(QObject* parent)
    : QObject(parent)
    , m_port(new QSerialPort(this))
{
    connect(m_port, &QSerialPort::readyRead, this, &RD03EDriver::onReadyRead);
    connect(m_port, &QSerialPort::errorOccurred, this, [this](QSerialPort::SerialPortError e) {
        if (e != QSerialPort::NoError)
            emit errorOccurred(m_port->errorString());
    });
}

RD03EDriver::~RD03EDriver() {
    close();
}

bool RD03EDriver::open(const QString& portName, qint32 baudRate) {
    if (m_port->isOpen())
        m_port->close();

    m_port->setPortName(portName);
    m_port->setBaudRate(baudRate);
    m_port->setDataBits(QSerialPort::Data8);
    m_port->setParity(QSerialPort::NoParity);
    m_port->setStopBits(QSerialPort::OneStop);
    m_port->setFlowControl(QSerialPort::NoFlowControl);

    if (!m_port->open(QIODevice::ReadWrite)) {
        emit errorOccurred(m_port->errorString());
        return false;
    }
    m_rxBuf.clear();
    return true;
}

void RD03EDriver::close() {
    if (m_port->isOpen())
        m_port->close();
    m_rxBuf.clear();
}

bool RD03EDriver::isOpen() const {
    return m_port->isOpen();
}

QString RD03EDriver::portName() const {
    return m_port->portName();
}

void RD03EDriver::onReadyRead() {
    m_rxBuf.append(m_port->readAll());
    parseBuffer();
}

void RD03EDriver::parseBuffer() {
    // Frame: AA AA status distLow distHigh 55 55  (7 bytes)
    while (m_rxBuf.size() >= 7) {
        // Find frame start AA AA
        int idx = -1;
        for (int i = 0; i <= m_rxBuf.size() - 7; ++i) {
            if ((uint8_t)m_rxBuf[i]   == 0xAA &&
                (uint8_t)m_rxBuf[i+1] == 0xAA &&
                (uint8_t)m_rxBuf[i+5] == 0x55 &&
                (uint8_t)m_rxBuf[i+6] == 0x55)
            {
                idx = i;
                break;
            }
        }
        if (idx < 0) {
            // No valid frame; discard all but last 6 bytes
            if (m_rxBuf.size() > 6)
                m_rxBuf.remove(0, m_rxBuf.size() - 6);
            return;
        }
        // Discard bytes before frame
        if (idx > 0)
            m_rxBuf.remove(0, idx);

        // Extract frame
        RadarFrame f;
        f.status = (uint8_t)m_rxBuf[2];
        uint16_t rawDist = (uint8_t)m_rxBuf[3] | ((uint8_t)m_rxBuf[4] << 8);
        f.distanceMeters = rawDist / 100.0f;
        f.timestamp = QDateTime::currentMSecsSinceEpoch();

        emit frameReceived(f);
        m_rxBuf.remove(0, 7);
    }
}
