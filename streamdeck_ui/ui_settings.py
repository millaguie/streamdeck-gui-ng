################################################################################
## Form generated from reading UI file 'settings.ui'
##
## Created by: Qt User Interface Compiler version 6.10.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QLabel, QSizePolicy,
    QSlider, QVBoxLayout, QWidget)
from . import resources_rc

class Ui_SettingsDialog:
    def setupUi(self, SettingsDialog):
        if not SettingsDialog.objectName():
            SettingsDialog.setObjectName("SettingsDialog")
        SettingsDialog.setWindowModality(Qt.ApplicationModal)
        SettingsDialog.resize(452, 156)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(SettingsDialog.sizePolicy().hasHeightForWidth())
        SettingsDialog.setSizePolicy(sizePolicy)
        icon = QIcon()
        icon.addFile(":/icons/icons/gear.png", QSize(), QIcon.Mode.Normal, QIcon.State.Off)
        SettingsDialog.setWindowIcon(icon)
        self.verticalLayout = QVBoxLayout(SettingsDialog)
        self.verticalLayout.setObjectName("verticalLayout")
        self.verticalLayout.setContentsMargins(9, -1, -1, -1)
        self.verticalLayout_2 = QVBoxLayout()
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.formLayout = QFormLayout()
        self.formLayout.setObjectName("formLayout")
        self.formLayout.setHorizontalSpacing(30)
        self.formLayout.setVerticalSpacing(6)
        self.label = QLabel(SettingsDialog)
        self.label.setObjectName("label")

        self.formLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.label)

        self.label_streamdeck = QLabel(SettingsDialog)
        self.label_streamdeck.setObjectName("label_streamdeck")

        self.formLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.label_streamdeck)

        self.label_brightness = QLabel(SettingsDialog)
        self.label_brightness.setObjectName("label_brightness")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.label_brightness)

        self.brightness = QSlider(SettingsDialog)
        self.brightness.setObjectName("brightness")
        self.brightness.setOrientation(Qt.Horizontal)

        self.formLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.brightness)

        self.label_dim = QLabel(SettingsDialog)
        self.label_dim.setObjectName("label_dim")

        self.formLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.label_dim)

        self.dim = QComboBox(SettingsDialog)
        self.dim.setObjectName("dim")

        self.formLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.dim)

        self.label_brightness_dimmed = QLabel(SettingsDialog)
        self.label_brightness_dimmed.setObjectName("label_brightness_dimmed")

        self.formLayout.setWidget(3, QFormLayout.ItemRole.LabelRole, self.label_brightness_dimmed)

        self.brightness_dimmed = QSlider(SettingsDialog)
        self.brightness_dimmed.setObjectName("brightness_dimmed")
        self.brightness_dimmed.setOrientation(Qt.Horizontal)

        self.formLayout.setWidget(3, QFormLayout.ItemRole.FieldRole, self.brightness_dimmed)


        self.verticalLayout_2.addLayout(self.formLayout)


        self.verticalLayout.addLayout(self.verticalLayout_2)

        self.buttonBox = QDialogButtonBox(SettingsDialog)
        self.buttonBox.setObjectName("buttonBox")
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel|QDialogButtonBox.Ok)
        self.buttonBox.setCenterButtons(False)

        self.verticalLayout.addWidget(self.buttonBox)


        self.retranslateUi(SettingsDialog)
        self.buttonBox.accepted.connect(SettingsDialog.accept)
        self.buttonBox.rejected.connect(SettingsDialog.reject)

        QMetaObject.connectSlotsByName(SettingsDialog)
    # setupUi

    def retranslateUi(self, SettingsDialog):
        SettingsDialog.setWindowTitle(QCoreApplication.translate("SettingsDialog", "Stream Deck Settings", None))
        self.label.setText(QCoreApplication.translate("SettingsDialog", "Stream Deck:", None))
        self.label_streamdeck.setText("")
        self.label_brightness.setText(QCoreApplication.translate("SettingsDialog", "Brightness:", None))
        self.label_dim.setText(QCoreApplication.translate("SettingsDialog", "Auto dim after:", None))
        self.dim.setCurrentText("")
        self.label_brightness_dimmed.setText(QCoreApplication.translate("SettingsDialog", "Dim to %:", None))
    # retranslateUi

