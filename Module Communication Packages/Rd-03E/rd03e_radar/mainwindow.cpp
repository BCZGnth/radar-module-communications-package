#include "mainwindow.h"

#include <QApplication>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QGroupBox>
#include <QComboBox>
#include <QPushButton>
#include <QLabel>
#include <QSpinBox>
#include <QDoubleSpinBox>
#include <QTextEdit>
#include <QSplitter>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QHeaderView>
#include <QFileDialog>
#include <QMessageBox>
#include <QToolBar>
#include <QStatusBar>
#include <QDateTime>
#include <QTextStream>
#include <QFile>
#include <QTimer>
#include <QSerialPortInfo>
#include <QFrame>
#include <QDockWidget>
#include <QFont>
#include <QPalette>

#include <QtCharts/QChartView>
#include <QtCharts/QLineSeries>
#include <QtCharts/QChart>
#include <QtCharts/QValueAxis>

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
    , m_driver(new RD03EDriver(this))
{
    setWindowTitle("RD-03E Radar Analyzer");
    setMinimumSize(1100, 720);
    setupUi();
    setupToolbar();
    setConnectedState(false);
    onRefreshPorts();

    connect(m_driver, &RD03EDriver::frameReceived, this, &MainWindow::onFrameReceived);
    connect(m_driver, &RD03EDriver::errorOccurred,  this, &MainWindow::onErrorOccurred);
}

MainWindow::~MainWindow() {}

// ──────────────────────────────────────────────
//  UI Setup
// ──────────────────────────────────────────────
void MainWindow::setupUi() {
    // ── Central widget ──
    QWidget* central = new QWidget(this);
    setCentralWidget(central);
    QVBoxLayout* mainLayout = new QVBoxLayout(central);
    mainLayout->setContentsMargins(8, 8, 8, 8);
    mainLayout->setSpacing(6);

    // ── Top control row ──
    QHBoxLayout* topRow = new QHBoxLayout();

    // Port group
    QGroupBox* portGroup = new QGroupBox("Serial Port");
    QHBoxLayout* portLayout = new QHBoxLayout(portGroup);
    m_portCombo  = new QComboBox(); m_portCombo->setMinimumWidth(130);
    m_baudCombo  = new QComboBox();
    m_baudCombo->addItems({"256000","115200","57600","38400","19200","9600"});
    m_baudCombo->setCurrentText("256000");
    m_refreshBtn = new QPushButton("⟳");
    m_refreshBtn->setFixedWidth(32);
    m_refreshBtn->setToolTip("Refresh port list");
    m_connectBtn    = new QPushButton("Connect");
    m_disconnectBtn = new QPushButton("Disconnect");
    m_connectBtn->setStyleSheet("QPushButton{background:#2ecc71;color:white;font-weight:bold;border-radius:4px;padding:4px 12px;}"
                                "QPushButton:hover{background:#27ae60;}");
    m_disconnectBtn->setStyleSheet("QPushButton{background:#e74c3c;color:white;font-weight:bold;border-radius:4px;padding:4px 12px;}"
                                   "QPushButton:hover{background:#c0392b;}");
    portLayout->addWidget(new QLabel("Port:"));
    portLayout->addWidget(m_portCombo);
    portLayout->addWidget(m_refreshBtn);
    portLayout->addWidget(new QLabel("Baud:"));
    portLayout->addWidget(m_baudCombo);
    portLayout->addWidget(m_connectBtn);
    portLayout->addWidget(m_disconnectBtn);

    // Settings group
    QGroupBox* settingsGroup = new QGroupBox("Chart Settings");
    QHBoxLayout* settLayout = new QHBoxLayout(settingsGroup);
    m_windowSpin  = new QSpinBox();
    m_windowSpin->setRange(5, 600); m_windowSpin->setValue(30);
    m_windowSpin->setSuffix(" s"); m_windowSpin->setToolTip("Time window");
    m_maxDistSpin = new QDoubleSpinBox();
    m_maxDistSpin->setRange(0.1, 10.0); m_maxDistSpin->setValue(6.0);
    m_maxDistSpin->setSingleStep(0.5); m_maxDistSpin->setSuffix(" m");
    QPushButton* applyBtn = new QPushButton("Apply");
    applyBtn->setToolTip("Apply axis settings");
    settLayout->addWidget(new QLabel("Window:")); settLayout->addWidget(m_windowSpin);
    settLayout->addWidget(new QLabel("Max Dist:")); settLayout->addWidget(m_maxDistSpin);
    settLayout->addWidget(applyBtn);

    // Live readout
    QGroupBox* readoutGroup = new QGroupBox("Live Readout");
    QHBoxLayout* readLayout = new QHBoxLayout(readoutGroup);
    m_statusLight   = new QLabel("●"); m_statusLight->setFixedWidth(20);
    m_distanceLabel  = new QLabel("-- m");
    m_presenceLabel  = new QLabel("None");
    m_frameCountLabel= new QLabel("Frames: 0");
    QFont bigFont = m_distanceLabel->font();
    bigFont.setPointSize(14); bigFont.setBold(true);
    m_distanceLabel->setFont(bigFont);
    readLayout->addWidget(m_statusLight);
    readLayout->addWidget(new QLabel("Dist:"));
    readLayout->addWidget(m_distanceLabel);
    readLayout->addWidget(new QLabel("Status:"));
    readLayout->addWidget(m_presenceLabel);
    readLayout->addWidget(m_frameCountLabel);

    topRow->addWidget(portGroup, 2);
    topRow->addWidget(settingsGroup, 2);
    topRow->addWidget(readoutGroup, 1);
    mainLayout->addLayout(topRow);

    // ── Splitter: chart top, table+log bottom ──
    QSplitter* vsplit = new QSplitter(Qt::Vertical);

    // Chart
    setupChart();
    vsplit->addWidget(m_chartView);

    // Bottom split: table | log
    QSplitter* hsplit = new QSplitter(Qt::Horizontal);

    m_tableWidget = new QTableWidget(0, 4);
    m_tableWidget->setHorizontalHeaderLabels({"Timestamp","Time (s)","Distance (m)","Status"});
    m_tableWidget->horizontalHeader()->setStretchLastSection(true);
    m_tableWidget->setAlternatingRowColors(true);
    m_tableWidget->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_tableWidget->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_tableWidget->setSortingEnabled(true);

    m_logView = new QTextEdit();
    m_logView->setReadOnly(true);
    m_logView->setMaximumHeight(160);
    m_logView->setPlaceholderText("Event log...");
    QFont monoFont("Courier New", 9);
    m_logView->setFont(monoFont);

    hsplit->addWidget(m_tableWidget);
    hsplit->addWidget(m_logView);
    hsplit->setSizes({600, 400});

    vsplit->addWidget(hsplit);
    vsplit->setSizes({420, 200});

    mainLayout->addWidget(vsplit, 1);

    // ── Connections ──
    connect(m_connectBtn,    &QPushButton::clicked, this, &MainWindow::onConnectClicked);
    connect(m_disconnectBtn, &QPushButton::clicked, this, &MainWindow::onDisconnectClicked);
    connect(m_refreshBtn,    &QPushButton::clicked, this, &MainWindow::onRefreshPorts);
    connect(applyBtn,        &QPushButton::clicked, this, &MainWindow::onUpdateAxes);

    // Status bar
    statusBar()->showMessage("Disconnected");
}

void MainWindow::setupChart() {
    m_chart = new QChart();
    m_chart->setTitle("RD-03E Distance Over Time");
    m_chart->setTheme(QChart::ChartThemeDark);
    m_chart->legend()->hide();

    m_distSeries = new QLineSeries();
    m_distSeries->setName("Distance");
    QPen pen(QColor(0x00, 0xd4, 0xff));
    pen.setWidth(2);
    m_distSeries->setPen(pen);

    m_chart->addSeries(m_distSeries);

    m_axisX = new QValueAxis();
    m_axisX->setTitleText("Time (s)");
    m_axisX->setRange(0, 30);
    m_axisX->setLabelFormat("%.1f");
    m_axisX->setTickCount(7);
    m_chart->addAxis(m_axisX, Qt::AlignBottom);
    m_distSeries->attachAxis(m_axisX);

    m_axisY = new QValueAxis();
    m_axisY->setTitleText("Distance (m)");
    m_axisY->setRange(0, 6);
    m_axisY->setLabelFormat("%.2f");
    m_axisY->setTickCount(7);
    m_chart->addAxis(m_axisY, Qt::AlignLeft);
    m_distSeries->attachAxis(m_axisY);

    m_chartView = new QChartView(m_chart);
    m_chartView->setRenderHint(QPainter::Antialiasing);
    m_chartView->setMinimumHeight(300);
}

void MainWindow::setupToolbar() {
    QToolBar* tb = addToolBar("Main");
    tb->setMovable(false);

    QAction* saveAct = tb->addAction(QIcon::fromTheme("document-save", style()->standardIcon(QStyle::SP_DialogSaveButton)), "Save Test");
    QAction* openAct = tb->addAction(QIcon::fromTheme("document-open", style()->standardIcon(QStyle::SP_DialogOpenButton)), "Open Test");
    tb->addSeparator();
    QAction* clearAct = tb->addAction(QIcon::fromTheme("edit-clear",   style()->standardIcon(QStyle::SP_TrashIcon)), "Clear Data");

    connect(saveAct,  &QAction::triggered, this, &MainWindow::onSaveTest);
    connect(openAct,  &QAction::triggered, this, &MainWindow::onOpenTest);
    connect(clearAct, &QAction::triggered, this, &MainWindow::onClearData);
}

// ──────────────────────────────────────────────
//  State helpers
// ──────────────────────────────────────────────
void MainWindow::setConnectedState(bool connected) {
    m_connectBtn->setEnabled(!connected);
    m_disconnectBtn->setEnabled(connected);
    m_portCombo->setEnabled(!connected);
    m_baudCombo->setEnabled(!connected);
    m_refreshBtn->setEnabled(!connected);
    statusBar()->showMessage(connected
        ? QString("Connected to %1").arg(m_driver->portName())
        : "Disconnected");
    if (!connected) {
        m_statusLight->setStyleSheet("QLabel{color:#888;}");
        m_presenceLabel->setText("None");
    }
}

void MainWindow::log(const QString& msg) {
    QString ts = QDateTime::currentDateTime().toString("hh:mm:ss.zzz");
    m_logView->append(QString("[%1] %2").arg(ts, msg));
}

// ──────────────────────────────────────────────
//  Slots
// ──────────────────────────────────────────────
void MainWindow::onRefreshPorts() {
    m_portCombo->clear();
    const auto ports = QSerialPortInfo::availablePorts();
    if (ports.isEmpty()) {
        m_portCombo->addItem("(none)");
    } else {
        for (const auto& p : ports) {
            m_portCombo->addItem(
                QString("%1 — %2").arg(p.portName(), p.description().isEmpty() ? "Serial Port" : p.description()),
                p.portName());
        }
    }
    log(QString("Found %1 serial port(s)").arg(ports.size()));
}

void MainWindow::onConnectClicked() {
    QString portName = m_portCombo->currentData().toString();
    if (portName.isEmpty()) portName = m_portCombo->currentText().split(" ").first();
    qint32 baud = m_baudCombo->currentText().toInt();

    if (m_driver->open(portName, baud)) {
        setConnectedState(true);
        m_startTime = 0; // reset on new session
        log(QString("Opened %1 @ %2 baud").arg(portName).arg(baud));
    } else {
        log("Connection failed");
    }
}

void MainWindow::onDisconnectClicked() {
    m_driver->close();
    setConnectedState(false);
    log("Disconnected");
}

void MainWindow::onFrameReceived(const RadarFrame& f) {
    if (m_startTime == 0) m_startTime = f.timestamp;
    addDataPoint(f);
    m_frameCount++;

    // Live readout
    m_distanceLabel->setText(QString("%1 m").arg(f.distanceMeters, 0, 'f', 2));
    m_frameCountLabel->setText(QString("Frames: %1").arg(m_frameCount));
    m_presenceLabel->setText(f.statusString());

    switch (f.status) {
        case 2:  m_statusLight->setStyleSheet("QLabel{color:#e74c3c;font-size:18px;}"); break;
        case 1:  m_statusLight->setStyleSheet("QLabel{color:#f39c12;font-size:18px;}"); break;
        default: m_statusLight->setStyleSheet("QLabel{color:#2ecc71;font-size:18px;}"); break;
    }
}

void MainWindow::onErrorOccurred(const QString& msg) {
    log("ERROR: " + msg);
    setConnectedState(false);
}

void MainWindow::onUpdateAxes() {
    int window = m_windowSpin->value();
    double maxDist = m_maxDistSpin->value();

    if (!m_data.isEmpty() && m_startTime > 0) {
        double lastT = (m_data.last().ts - m_startTime) / 1000.0;
        double xMin = qMax(0.0, lastT - window);
        m_axisX->setRange(xMin, xMin + window);
    } else {
        m_axisX->setRange(0, window);
    }
    m_axisY->setRange(0, maxDist);
}

void MainWindow::onClearData() {
    if (QMessageBox::question(this, "Clear Data", "Clear all recorded data?",
            QMessageBox::Yes | QMessageBox::No) != QMessageBox::Yes) return;
    m_data.clear();
    m_distSeries->clear();
    m_tableWidget->setRowCount(0);
    m_frameCount = 0;
    m_startTime  = 0;
    m_frameCountLabel->setText("Frames: 0");
    m_distanceLabel->setText("-- m");
    log("Data cleared");
}

// ──────────────────────────────────────────────
//  Data + chart
// ──────────────────────────────────────────────
void MainWindow::addDataPoint(const RadarFrame& f) {
    if (m_startTime == 0) m_startTime = f.timestamp;
    double t = (f.timestamp - m_startTime) / 1000.0;

    // Store
    m_data.append({f.timestamp, f.distanceMeters, f.status});

    // Chart point
    m_distSeries->append(t, f.distanceMeters);

    // Scroll chart window
    int window = m_windowSpin->value();
    double xMin = qMax(0.0, t - window);
    m_axisX->setRange(xMin, xMin + window);
    m_axisY->setRange(0, m_maxDistSpin->value());

    // Table (prepend so newest is on top)
    m_tableWidget->setSortingEnabled(false);
    m_tableWidget->insertRow(0);
    QString tsStr = QDateTime::fromMSecsSinceEpoch(f.timestamp).toString("hh:mm:ss.zzz");
    m_tableWidget->setItem(0, 0, new QTableWidgetItem(tsStr));
    m_tableWidget->setItem(0, 1, new QTableWidgetItem(QString::number(t, 'f', 3)));
    m_tableWidget->setItem(0, 2, new QTableWidgetItem(QString::number(f.distanceMeters, 'f', 2)));
    m_tableWidget->setItem(0, 3, new QTableWidgetItem(f.statusString()));
    m_tableWidget->setSortingEnabled(true);

    // Limit table rows to 2000 to avoid UI slowdown
    if (m_tableWidget->rowCount() > 2000)
        m_tableWidget->setRowCount(2000);
}

// ──────────────────────────────────────────────
//  CSV Save / Open
// ──────────────────────────────────────────────
QString MainWindow::formatCsvHeader() {
    return "timestamp_ms,time_s,distance_m,status_code,status_text\n";
}

QString MainWindow::formatCsvRow(const RadarFrame& f) {
    if (m_startTime == 0) return {};
    double t = (f.timestamp - m_startTime) / 1000.0;
    return QString("%1,%2,%3,%4,%5\n")
        .arg(f.timestamp)
        .arg(t, 0, 'f', 4)
        .arg(f.distanceMeters, 0, 'f', 3)
        .arg(f.status)
        .arg(f.statusString());
}

void MainWindow::onSaveTest() {
    if (m_data.isEmpty()) {
        QMessageBox::information(this, "Save Test", "No data to save.");
        return;
    }

    QString defaultName = QString("rd03e_test_%1.csv")
        .arg(QDateTime::currentDateTime().toString("yyyyMMdd_hhmmss"));

    QString path = QFileDialog::getSaveFileName(this, "Save Test Data", defaultName,
        "CSV Files (*.csv);;All Files (*)");
    if (path.isEmpty()) return;

    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) {
        QMessageBox::critical(this, "Error", "Cannot write: " + f.errorString());
        return;
    }

    QTextStream out(&f);
    out << formatCsvHeader();
    for (const auto& dp : m_data) {
        RadarFrame rf; rf.timestamp = dp.ts; rf.distanceMeters = dp.dist; rf.status = dp.status;
        double t = (rf.timestamp - m_startTime) / 1000.0;
        out << QString("%1,%2,%3,%4,%5\n")
            .arg(rf.timestamp)
            .arg(t, 0, 'f', 4)
            .arg(rf.distanceMeters, 0, 'f', 3)
            .arg(rf.status)
            .arg(rf.statusString());
    }
    f.close();
    log(QString("Saved %1 rows → %2").arg(m_data.size()).arg(path));
    QMessageBox::information(this, "Save Test", QString("Saved %1 frames to:\n%2").arg(m_data.size()).arg(path));
}

void MainWindow::onOpenTest() {
    QString path = QFileDialog::getOpenFileName(this, "Open Test Data", "",
        "CSV Files (*.csv);;All Files (*)");
    if (path.isEmpty()) return;

    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) {
        QMessageBox::critical(this, "Error", "Cannot read: " + f.errorString());
        return;
    }

    // Clear existing data first
    m_data.clear();
    m_distSeries->clear();
    m_tableWidget->setRowCount(0);
    m_frameCount = 0;
    m_startTime  = 0;

    QTextStream in(&f);
    QString header = in.readLine(); // skip header
    Q_UNUSED(header);

    int loaded = 0;
    while (!in.atEnd()) {
        QString line = in.readLine().trimmed();
        if (line.isEmpty()) continue;
        QStringList cols = line.split(',');
        if (cols.size() < 4) continue;

        bool ok1, ok2, ok3, ok4;
        qint64 ts   = cols[0].toLongLong(&ok1);
        float  dist = cols[2].toFloat(&ok3);
        int    stat = cols[3].toInt(&ok4);
        Q_UNUSED(ok2);

        if (!ok1 || !ok3 || !ok4) continue;

        if (m_startTime == 0) m_startTime = ts;
        m_data.append({ts, dist, stat});
        loaded++;
    }
    f.close();

    // Rebuild chart and table from loaded data
    rebuildChart();
    log(QString("Loaded %1 frames from %2").arg(loaded).arg(path));
    statusBar()->showMessage(QString("Viewing saved test: %1 (%2 frames)").arg(QFileInfo(path).fileName()).arg(loaded));
    QMessageBox::information(this, "Open Test", QString("Loaded %1 frames from:\n%2").arg(loaded).arg(path));
}

void MainWindow::rebuildChart() {
    m_distSeries->clear();
    m_tableWidget->setRowCount(0);

    if (m_data.isEmpty()) return;

    m_tableWidget->setSortingEnabled(false);
    for (const auto& dp : m_data) {
        double t = (dp.ts - m_startTime) / 1000.0;
        m_distSeries->append(t, dp.dist);

        int row = m_tableWidget->rowCount();
        m_tableWidget->insertRow(row);
        QString tsStr = QDateTime::fromMSecsSinceEpoch(dp.ts).toString("hh:mm:ss.zzz");
        m_tableWidget->setItem(row, 0, new QTableWidgetItem(tsStr));
        m_tableWidget->setItem(row, 1, new QTableWidgetItem(QString::number(t, 'f', 3)));
        m_tableWidget->setItem(row, 2, new QTableWidgetItem(QString::number(dp.dist, 'f', 2)));
        QString statStr = (dp.status == 2) ? "Moving" : (dp.status == 1 ? "Static" : "None");
        m_tableWidget->setItem(row, 3, new QTableWidgetItem(statStr));
    }
    m_tableWidget->setSortingEnabled(true);

    // Fit axes to data
    double lastT = (m_data.last().ts - m_startTime) / 1000.0;
    m_axisX->setRange(0, lastT > 0 ? lastT : 30);

    float maxDist = 0;
    for (const auto& dp : m_data) maxDist = qMax(maxDist, dp.dist);
    m_axisY->setRange(0, qMax((float)m_maxDistSpin->value(), maxDist * 1.1f));

    m_frameCount = m_data.size();
    m_frameCountLabel->setText(QString("Frames: %1").arg(m_frameCount));
}
