#include <QApplication>
#include <QStyleFactory>
#include "mainwindow.h"

int main(int argc, char* argv[]) {
    QApplication app(argc, argv);

    app.setApplicationName("RD-03E Radar Analyzer");
    app.setApplicationVersion("1.0");
    app.setOrganizationName("RadarTools");

    // Use Fusion style for consistent cross-platform look
    app.setStyle(QStyleFactory::create("Fusion"));

    // Dark palette
    QPalette darkPalette;
    darkPalette.setColor(QPalette::Window,          QColor(30, 30, 35));
    darkPalette.setColor(QPalette::WindowText,       QColor(220, 220, 220));
    darkPalette.setColor(QPalette::Base,             QColor(22, 22, 28));
    darkPalette.setColor(QPalette::AlternateBase,    QColor(38, 38, 45));
    darkPalette.setColor(QPalette::ToolTipBase,      QColor(50, 50, 60));
    darkPalette.setColor(QPalette::ToolTipText,      QColor(220, 220, 220));
    darkPalette.setColor(QPalette::Text,             QColor(220, 220, 220));
    darkPalette.setColor(QPalette::Button,           QColor(45, 45, 55));
    darkPalette.setColor(QPalette::ButtonText,       QColor(220, 220, 220));
    darkPalette.setColor(QPalette::BrightText,       Qt::red);
    darkPalette.setColor(QPalette::Link,             QColor(0, 180, 220));
    darkPalette.setColor(QPalette::Highlight,        QColor(0, 140, 200));
    darkPalette.setColor(QPalette::HighlightedText,  Qt::white);
    app.setPalette(darkPalette);

    MainWindow w;
    w.show();
    return app.exec();
}
