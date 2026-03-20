#pragma once

#include <QMainWindow>
#include <QVector>
#include <QTimer>
#include <QDateTime>
#include "rd03e_driver.h"

QT_BEGIN_NAMESPACE
class QComboBox;
class QPushButton;
class QLabel;
class QChartView;
class QLineSeries;
class QChart;
class QValueAxis;
class QStatusBar;
class QSplitter;
class QTableWidget;
class QSpinBox;
class QDoubleSpinBox;
class QGroupBox;
class QTextEdit;
QT_END_NAMESPACE

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow();

private slots:
    void onConnectClicked();
    void onDisconnectClicked();
    void onRefreshPorts();
    void onFrameReceived(const RadarFrame& frame);
    void onErrorOccurred(const QString& msg);
    void onSaveTest();
    void onOpenTest();
    void onClearData();
    void onUpdateAxes();

private:
    void setupUi();
    void setupChart();
    void setupToolbar();
    void setupStatusBar();
    void addDataPoint(const RadarFrame& f);
    void rebuildChart();
    void setConnectedState(bool connected);
    void log(const QString& msg);
    QString formatCsvHeader();
    QString formatCsvRow(const RadarFrame& f);

    // Serial
    RD03EDriver*  m_driver;

    // UI controls
    QComboBox*    m_portCombo;
    QComboBox*    m_baudCombo;
    QPushButton*  m_connectBtn;
    QPushButton*  m_disconnectBtn;
    QPushButton*  m_refreshBtn;

    // Chart
    QChartView*   m_chartView;
    QChart*       m_chart;
    QLineSeries*  m_distSeries;
    QValueAxis*   m_axisX;
    QValueAxis*   m_axisY;

    // Status indicators
    QLabel*       m_statusLight;
    QLabel*       m_distanceLabel;
    QLabel*       m_presenceLabel;
    QLabel*       m_frameCountLabel;

    // Settings
    QSpinBox*     m_windowSpin;   // visible time window (seconds)
    QDoubleSpinBox* m_maxDistSpin; // max distance on Y axis (m)

    // Log
    QTextEdit*    m_logView;

    // Table
    QTableWidget* m_tableWidget;

    // Data store
    struct DataPoint { qint64 ts; float dist; int status; };
    QVector<DataPoint> m_data;
    int m_frameCount = 0;

    // Timing reference
    qint64 m_startTime = 0;
};
