# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'leerrohr_verlegen_dialog_base.ui'
#
# Created by: PyQt5 UI code generator 5.15.10
#
# WARNING: Any manual changes made to this file will be lost when pyuic5 is
# run again.  Do not edit this file unless you know what you are doing.


from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_LeerrohrVerlegungsToolDialogBase(object):
    def setupUi(self, LeerrohrVerlegungsToolDialogBase):
        LeerrohrVerlegungsToolDialogBase.setObjectName("LeerrohrVerlegungsToolDialogBase")
        LeerrohrVerlegungsToolDialogBase.resize(525, 720)
        self.tabWidget = QtWidgets.QTabWidget(LeerrohrVerlegungsToolDialogBase)
        self.tabWidget.setGeometry(QtCore.QRect(0, 0, 531, 731))
        font = QtGui.QFont()
        font.setBold(False)
        font.setItalic(False)
        font.setWeight(50)
        self.tabWidget.setFont(font)
        self.tabWidget.setAutoFillBackground(False)
        self.tabWidget.setObjectName("tabWidget")
        self.tab = QtWidgets.QWidget()
        self.tab.setObjectName("tab")
        self.groupBox_3 = QtWidgets.QGroupBox(self.tab)
        self.groupBox_3.setGeometry(QtCore.QRect(10, 530, 491, 91))
        self.groupBox_3.setObjectName("groupBox_3")
        self.pushButton_Datenpruefung = QtWidgets.QPushButton(self.groupBox_3)
        self.pushButton_Datenpruefung.setGeometry(QtCore.QRect(406, 30, 84, 25))
        self.pushButton_Datenpruefung.setObjectName("pushButton_Datenpruefung")
        self.label_Pruefung = QtWidgets.QTextEdit(self.groupBox_3)
        self.label_Pruefung.setGeometry(QtCore.QRect(1, 66, 490, 25))
        self.label_Pruefung.setObjectName("label_Pruefung")
        self.pushButton_Import = QtWidgets.QPushButton(self.tab)
        self.pushButton_Import.setGeometry(QtCore.QRect(333, 654, 84, 23))
        self.pushButton_Import.setObjectName("pushButton_Import")
        self.button_box = QtWidgets.QDialogButtonBox(self.tab)
        self.button_box.setGeometry(QtCore.QRect(10, 650, 491, 32))
        self.button_box.setOrientation(QtCore.Qt.Horizontal)
        self.button_box.setStandardButtons(QtWidgets.QDialogButtonBox.Cancel|QtWidgets.QDialogButtonBox.Reset)
        self.button_box.setObjectName("button_box")
        self.groupBox_2 = QtWidgets.QGroupBox(self.tab)
        self.groupBox_2.setGeometry(QtCore.QRect(10, 230, 491, 291))
        self.groupBox_2.setObjectName("groupBox_2")
        self.label_11 = QtWidgets.QLabel(self.groupBox_2)
        self.label_11.setGeometry(QtCore.QRect(10, 94, 101, 16))
        self.label_11.setObjectName("label_11")
        self.label_12 = QtWidgets.QLabel(self.groupBox_2)
        self.label_12.setGeometry(QtCore.QRect(10, 30, 111, 20))
        self.label_12.setObjectName("label_12")
        self.comboBox_Verbundnummer = QtWidgets.QComboBox(self.groupBox_2)
        self.comboBox_Verbundnummer.setGeometry(QtCore.QRect(130, 29, 181, 22))
        self.comboBox_Verbundnummer.setObjectName("comboBox_Verbundnummer")
        self.label_13 = QtWidgets.QLabel(self.groupBox_2)
        self.label_13.setGeometry(QtCore.QRect(10, 64, 111, 16))
        self.label_13.setObjectName("label_13")
        self.comboBox_Gefoerdert = QtWidgets.QComboBox(self.groupBox_2)
        self.comboBox_Gefoerdert.setGeometry(QtCore.QRect(130, 60, 181, 22))
        self.comboBox_Gefoerdert.setObjectName("comboBox_Gefoerdert")
        self.label_14 = QtWidgets.QLabel(self.groupBox_2)
        self.label_14.setGeometry(QtCore.QRect(10, 125, 121, 16))
        self.label_14.setObjectName("label_14")
        self.label_Kommentar = QtWidgets.QLineEdit(self.groupBox_2)
        self.label_Kommentar.setGeometry(QtCore.QRect(130, 90, 351, 22))
        self.label_Kommentar.setText("")
        self.label_Kommentar.setObjectName("label_Kommentar")
        self.label_Kommentar_2 = QtWidgets.QLineEdit(self.groupBox_2)
        self.label_Kommentar_2.setGeometry(QtCore.QRect(130, 120, 351, 22))
        self.label_Kommentar_2.setText("")
        self.label_Kommentar_2.setObjectName("label_Kommentar_2")
        self.mDateTimeEdit_Strecke = QgsDateTimeEdit(self.groupBox_2)
        self.mDateTimeEdit_Strecke.setGeometry(QtCore.QRect(130, 150, 181, 22))
        self.mDateTimeEdit_Strecke.setObjectName("mDateTimeEdit_Strecke")
        self.label_22 = QtWidgets.QLabel(self.groupBox_2)
        self.label_22.setGeometry(QtCore.QRect(10, 155, 121, 16))
        self.label_22.setObjectName("label_22")
        self.label_15 = QtWidgets.QLabel(self.groupBox_2)
        self.label_15.setGeometry(QtCore.QRect(10, 184, 111, 16))
        self.label_15.setObjectName("label_15")
        self.comboBox_Subduct = QtWidgets.QComboBox(self.groupBox_2)
        self.comboBox_Subduct.setGeometry(QtCore.QRect(130, 180, 181, 22))
        self.comboBox_Subduct.setObjectName("comboBox_Subduct")
        self.groupBox = QtWidgets.QGroupBox(self.tab)
        self.groupBox.setGeometry(QtCore.QRect(10, 150, 491, 71))
        self.groupBox.setObjectName("groupBox")
        self.pushButton_verlauf = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_verlauf.setGeometry(QtCore.QRect(10, 24, 84, 25))
        self.pushButton_verlauf.setObjectName("pushButton_verlauf")
        self.label_verlauf = QtWidgets.QTextEdit(self.groupBox)
        self.label_verlauf.setGeometry(QtCore.QRect(230, 24, 251, 25))
        self.label_verlauf.setObjectName("label_verlauf")
        self.label_8 = QtWidgets.QLabel(self.groupBox)
        self.label_8.setGeometry(QtCore.QRect(129, 28, 91, 20))
        self.label_8.setObjectName("label_8")
        self.groupBox_4 = QtWidgets.QGroupBox(self.tab)
        self.groupBox_4.setGeometry(QtCore.QRect(10, 10, 491, 131))
        self.groupBox_4.setObjectName("groupBox_4")
        self.label = QtWidgets.QLabel(self.groupBox_4)
        self.label.setGeometry(QtCore.QRect(10, 20, 121, 16))
        self.label.setObjectName("label")
        self.comboBox_leerrohr_typ = QtWidgets.QComboBox(self.groupBox_4)
        self.comboBox_leerrohr_typ.setGeometry(QtCore.QRect(10, 40, 181, 22))
        self.comboBox_leerrohr_typ.setObjectName("comboBox_leerrohr_typ")
        self.label_gewaehltes_leerrohr = QtWidgets.QTextEdit(self.groupBox_4)
        self.label_gewaehltes_leerrohr.setGeometry(QtCore.QRect(230, 40, 251, 22))
        self.label_gewaehltes_leerrohr.setObjectName("label_gewaehltes_leerrohr")
        self.label_6 = QtWidgets.QLabel(self.groupBox_4)
        self.label_6.setGeometry(QtCore.QRect(230, 20, 111, 16))
        self.label_6.setObjectName("label_6")
        self.comboBox_leerrohr_typ_2 = QtWidgets.QComboBox(self.groupBox_4)
        self.comboBox_leerrohr_typ_2.setGeometry(QtCore.QRect(10, 90, 181, 22))
        self.comboBox_leerrohr_typ_2.setObjectName("comboBox_leerrohr_typ_2")
        self.label_gewaehltes_leerrohr_2 = QtWidgets.QTextEdit(self.groupBox_4)
        self.label_gewaehltes_leerrohr_2.setGeometry(QtCore.QRect(230, 90, 251, 22))
        self.label_gewaehltes_leerrohr_2.setObjectName("label_gewaehltes_leerrohr_2")
        self.label_10 = QtWidgets.QLabel(self.groupBox_4)
        self.label_10.setGeometry(QtCore.QRect(230, 70, 141, 16))
        self.label_10.setObjectName("label_10")
        self.label_2 = QtWidgets.QLabel(self.groupBox_4)
        self.label_2.setGeometry(QtCore.QRect(10, 70, 161, 16))
        self.label_2.setObjectName("label_2")
        self.groupBox_3.raise_()
        self.button_box.raise_()
        self.groupBox_2.raise_()
        self.groupBox.raise_()
        self.groupBox_4.raise_()
        self.pushButton_Import.raise_()
        self.tabWidget.addTab(self.tab, "")

        self.retranslateUi(LeerrohrVerlegungsToolDialogBase)
        self.tabWidget.setCurrentIndex(0)
        QtCore.QMetaObject.connectSlotsByName(LeerrohrVerlegungsToolDialogBase)

    def retranslateUi(self, LeerrohrVerlegungsToolDialogBase):
        _translate = QtCore.QCoreApplication.translate
        LeerrohrVerlegungsToolDialogBase.setWindowTitle(_translate("LeerrohrVerlegungsToolDialogBase", "Leerrohr Verlegen"))
        self.groupBox_3.setTitle(_translate("LeerrohrVerlegungsToolDialogBase", "Leerohr Prüfen:"))
        self.pushButton_Datenpruefung.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Daten Prüfen"))
        self.pushButton_Import.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Import"))
        self.groupBox_2.setTitle(_translate("LeerrohrVerlegungsToolDialogBase", "Attribute Leerrohr"))
        self.label_11.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Kommentar:"))
        self.label_12.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Verbundnummer:"))
        self.label_13.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Gefördert:"))
        self.label_14.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Beschreibung:"))
        self.label_Kommentar.setPlaceholderText(_translate("LeerrohrVerlegungsToolDialogBase", "Kommentar hier eingeben..."))
        self.label_Kommentar_2.setPlaceholderText(_translate("LeerrohrVerlegungsToolDialogBase", "Beschreibung hier eingeben..."))
        self.mDateTimeEdit_Strecke.setDisplayFormat(_translate("LeerrohrVerlegungsToolDialogBase", "dd.MM.yyyy"))
        self.label_22.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Verlegt am:"))
        self.label_15.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Subduct:"))
        self.groupBox.setTitle(_translate("LeerrohrVerlegungsToolDialogBase", "Verlauf Leerrohr"))
        self.pushButton_verlauf.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Leerrohrverlauf"))
        self.label_8.setText(_translate("LeerrohrVerlegungsToolDialogBase", "gewählte Trassen:"))
        self.groupBox_4.setTitle(_translate("LeerrohrVerlegungsToolDialogBase", "Auswahl Leerrohr"))
        self.label.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Leerrohrtyp auswählen:"))
        self.label_6.setText(_translate("LeerrohrVerlegungsToolDialogBase", "gewählter Leerrohrtyp:"))
        self.label_10.setText(_translate("LeerrohrVerlegungsToolDialogBase", "gewählter Leerrohrsubtyp:"))
        self.label_2.setText(_translate("LeerrohrVerlegungsToolDialogBase", "Leerrohrsubtyp auswählen:"))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab), _translate("LeerrohrVerlegungsToolDialogBase", "Leerrohr Verlauf"))
from qgsdatetimeedit import QgsDateTimeEdit