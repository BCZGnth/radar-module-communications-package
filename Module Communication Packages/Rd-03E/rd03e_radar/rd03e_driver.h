#pragma once

#include <QObject>
#include <QSerialPort>
#include <QByteArray>
#include <QTimer>
#include <cstdint>

// RD-03E frame protocol:
//  AA AA [status] [distLow] [distHigh] 55 55
//  status: 0 = no presence, 1 = static, 2 = moving
//  distance: uint16 little-endian in cm  → divide by 100 for meters

struct RadarFrame {
    float   distanceMeters = 0.0f;
    int     status         = 0;      // 0=none, 1=static, 2=moving
    qint64  timestamp      = 0;      // msecs since epoch

    QString statusString() const {
        switch (status) {
            case 1: return "Static";
            case 2: return "Moving";
            default: return "None";
        }
    }
};

class RD03EDriver : public QObject {
    Q_OBJECT
public:
    explicit RD03EDriver(QObject* parent = nullptr);
    ~RD03EDriver();

    bool open(const QString& portName, qint32 baudRate = 256000);
    void close();
    bool isOpen() const;
    QString portName() const;

signals:
    void frameReceived(const RadarFrame& frame);
    void errorOccurred(const QString& msg);

private slots:
    void onReadyRead();

private:
    void parseBuffer();

    QSerialPort* m_port;
    QByteArray   m_rxBuf;
};
