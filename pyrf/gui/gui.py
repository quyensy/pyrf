"""
The main application window and GUI controls

``MainWindow`` creates and handles the ``File | Open Device`` menu and
wraps the ``MainPanel`` widget responsible for most of the interface.

All the buttons and controls and their callback functions are built in
``MainPanel`` and arranged on a grid.  A ``Pyqtgraph Window`` is created
and placed to left of the controls.
"""

import sys
from PySide import QtGui, QtCore
import numpy as np
import math

from contextlib import contextmanager
from pkg_resources import parse_version

from pyrf.gui import colors
from pyrf.gui import labels
from pyrf.gui import gui_config
from pyrf.gui.controller import SpecAController

from pyrf.connectors.twisted_async import TwistedConnector
from pyrf.config import TriggerSettings, TRIGGER_TYPE_LEVEL
from pyrf.units import M
from pyrf.devices.thinkrf import WSA
from pyrf.vrt import (I_ONLY, VRT_IFDATA_I14Q14, VRT_IFDATA_I14,
    VRT_IFDATA_I24, VRT_IFDATA_PSD8)

from util import find_max_index, find_nearest_index
from util import hotkey_util, update_marker_traces
import control_util as cu
from plot_widget import Plot
from device_controls import DeviceControls
from frequency_controls import FrequencyControls
from discovery_widget import DiscoveryWidget

PLOT_YMIN = -140
PLOT_YMAX = 0

ZIF_BITS = 2**13
CONST_POINTS = 512
try:
    from twisted.internet.defer import inlineCallbacks
except ImportError:
    def inlineCallbacks(fn):
        pass


class MainWindow(QtGui.QMainWindow):
    """
    The main window and menus
    """
    def __init__(self, output_file=None):
        super(MainWindow, self).__init__()
        screen = QtGui.QDesktopWidget().screenGeometry()
        WINDOW_WIDTH = screen.width() * 0.7
        WINDOW_HEIGHT = screen.height() * 0.6
        self.resize(WINDOW_WIDTH,WINDOW_HEIGHT)

        self.controller = SpecAController()
        self.initUI()

    def initUI(self):
        name = None
        if len(sys.argv) > 1:
            name = sys.argv[1]
        self.mainPanel = MainPanel(self.controller)
        openAction = QtGui.QAction('&Open Device', self)
        openAction.triggered.connect(self.open_device_dialog)
        exitAction = QtGui.QAction('&Exit', self)
        exitAction.setShortcut('Ctrl+Q')
        exitAction.triggered.connect(self.close)
        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&File')
        fileMenu.addAction(openAction)
        fileMenu.addAction(exitAction)
        self.setWindowTitle('Spectrum Analyzer')
        self.setCentralWidget(self.mainPanel)
        if name:
            self.open_device(name, True)
        else:
            self.open_device_dialog()

    def open_device_dialog(self):
        self.discovery_widget = DiscoveryWidget(
            open_device_callback=self.open_device,
            name="Open Device")
        self.discovery_widget.show()

    @inlineCallbacks
    def open_device(self, name, ok):
        if not ok:
            self.show()
            return

        self.show()
        dut = WSA(connector=TwistedConnector(self._get_reactor()))
        yield dut.connect(name)

        if hasattr(dut.properties, 'MINIMUM_FW_VERSION') and parse_version(
                dut.fw_version) < parse_version(dut.properties.MINIMUM_FW_VERSION):
            too_old = QtGui.QMessageBox()
            too_old.setText('Your device firmware version is {0}'
                ' but this application is expecting at least version'
                ' {1}. Some features may not work properly'.format(
                dut.fw_version, dut.properties.MINIMUM_FW_VERSION))
            too_old.exec_()
        self.controller.set_device(dut)


    def closeEvent(self, event):
        if self.mainPanel.dut:
            self.mainPanel.dut.abort()
            self.mainPanel.dut.flush()
            self.mainPanel.dut.reset()
        event.accept()
        self._get_reactor().stop()

    def _get_reactor(self):
        # late import because installReactor is being used
        from twisted.internet import reactor
        return reactor


class MainPanel(QtGui.QWidget):
    """
    The spectrum view and controls
    """
    def __init__(self, controller):
        self.controller = controller
        controller.device_change.connect(self.device_changed)
        controller.state_change.connect(self.state_changed)
        controller.capture_receive.connect(self.capture_received)

        self.ref_level = 0
        self.dut = None
        self.control_widgets = []
        super(MainPanel, self).__init__()
        screen = QtGui.QDesktopWidget().screenGeometry()
        self.setMinimumWidth(screen.width() * 0.7)
        self.setMinimumHeight(screen.height() * 0.6)
        self.plot_state = None
        # plot window
        self._plot = Plot(self)
        self._marker_trace = None
        self._vrt_context = {}
        self.initUI()
        self.disable_controls()
        self.ref_level = 0
        self.plot_state = None

        self._plot_center_span = None, None

    def device_changed(self, dut):
        self.plot_state = gui_config.PlotState(dut.properties)
        self.dut_prop = dut.properties

        self.enable_controls()
        self._plot.const_window.show()
        self._plot.iq_window.show()

    def state_changed(self, state, changed):
        """
        signal handler for speca state changes
        :param state: new SpecAState object
        :param changed: list of attribute names changed
        """
        if 'mode' in changed:
            self.rfe_mode = state.rfe_mode()  # used by recentering code
            if state.sweeping():
                self._plot.const_window.hide()
                self._plot.iq_window.hide()
                return
            self._plot.const_window.show()
            self._plot.iq_window.show()

            cu._center_plot_view(self)

    def keyPressEvent(self, event):
        if self.dut:
            hotkey_util(self, event)

    def mousePressEvent(self, event):
        if self.dut:
            marker = self._plot.markers[self._marker_tab.currentIndex()]
            trace = self._plot.traces[marker.trace_index]
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                click_pos =  event.pos().x() - 68
                plot_window_width = self._plot.window.width() - 68

                if click_pos < plot_window_width and click_pos > 0:

                    window_freq = self._plot.view_box.viewRange()[0]
                    window_bw =  (window_freq[1] - window_freq[0])
                    click_freq = ((float(click_pos) / float(plot_window_width)) * float(window_bw)) + window_freq[0]
                    index = find_nearest_index(click_freq, trace.freq_range)
                    self._plot.markers[self._marker_tab.currentIndex()].data_index = index

    def initUI(self):
        grid = QtGui.QGridLayout()
        grid.setSpacing(10)
        for x in range(8):
            grid.setColumnMinimumWidth(x, 300)

        # add plot widget
        plot_width = 8

        grid.addWidget(self._plot_layout(),0,0,13,plot_width)

        self.marker_labels = []
        marker_label, delta_label, diff_label = self._marker_labels()
        self.marker_labels.append(marker_label)
        self.marker_labels.append(delta_label)
        grid.addWidget(marker_label, 0, 1, 1, 2)
        grid.addWidget(delta_label, 0, 3, 1, 2)
        grid.addWidget(diff_label , 0, 5, 1, 2)

        y = 0
        x = plot_width

        controls_layout = QtGui.QVBoxLayout()
        controls_layout.addWidget(self._trace_controls())
        controls_layout.addWidget(self._plot_controls())
        controls_layout.addWidget(self._device_controls())
        controls_layout.addWidget(self._freq_controls())
        controls_layout.addStretch()
        grid.addLayout(controls_layout, y, x, 13, 5)

        self._grid = grid

        self.setLayout(grid)

    def _plot_layout(self):
        vsplit = QtGui.QSplitter()
        vsplit.setOrientation(QtCore.Qt.Vertical)
        vsplit.addWidget(self._plot.window)

        hsplit = QtGui.QSplitter()
        hsplit.addWidget(self._plot.const_window)
        hsplit.addWidget(self._plot.iq_window)
        self._plot.const_window.heightForWidth(1)
        self._plot.const_window.hide()
        self._plot.iq_window.hide()
        vsplit.addWidget(hsplit)

        self._plot_layout = vsplit
        return self._plot_layout

    def _trace_controls(self):
        trace_group = QtGui.QGroupBox("Traces")

        self._trace_group = trace_group

        trace_controls_layout = QtGui.QVBoxLayout()

        # first row will contain the tabs
        first_row = QtGui.QHBoxLayout()

        # add tabs for each trace
        trace_tab = QtGui.QTabBar()
        count = 0
        for (trace,(r,g,b)) in zip(labels.TRACES, colors.TRACE_COLORS):
            trace_tab.addTab(trace)
            color = QtGui.QColor()
            color.setRgb(r,g,b)
            pixmap = QtGui.QPixmap(10,10)
            pixmap.fill(color)
            icon = QtGui.QIcon(pixmap)
            trace_tab.setTabIcon(count,icon)
            count += 1

        self._trace_tab = trace_tab
        trace_tab.currentChanged.connect(lambda: cu._trace_tab_change(self))

        self.control_widgets.append(self._trace_tab)
        first_row.addWidget(trace_tab)

        # second row contains the tab attributes
        second_row = QtGui.QHBoxLayout()
        max_hold, min_hold, write, store, blank  = self._trace_items()
        second_row.addWidget(max_hold)
        second_row.addWidget(min_hold)
        second_row.addWidget(write)
        second_row.addWidget(blank)
        second_row.addWidget(store)
        trace_controls_layout.addLayout(first_row)
        trace_controls_layout.addLayout(second_row) 
        trace_group.setLayout(trace_controls_layout)
        return trace_group

    def _trace_items(self):

        trace_attr = {}
        store = QtGui.QCheckBox('Store')
        store.clicked.connect(lambda: cu._store_trace(self))
        store.setEnabled(False)
        trace_attr['store'] = store

        max_hold = QtGui.QRadioButton('Max Hold')
        max_hold.clicked.connect(lambda: cu._max_hold(self))
        trace_attr['max_hold'] = max_hold

        min_hold = QtGui.QRadioButton('Min Hold')
        min_hold.clicked.connect(lambda: cu._min_hold(self))
        trace_attr['min_hold'] = min_hold

        write = QtGui.QRadioButton('Write')
        write.clicked.connect(lambda: cu._trace_write(self))
        trace_attr['write'] = write

        blank = QtGui.QRadioButton('Blank')
        blank.clicked.connect(lambda: cu._blank_trace(self))
        trace_attr['blank'] = blank

        self._trace_attr = trace_attr
        self._trace_attr['write'].click()
        return max_hold, min_hold, write, store, blank

    def _device_controls(self):
        self._dev_group = DeviceControls(self.controller)
        self.control_widgets.append(self._dev_group)
        return self._dev_group

    def _freq_controls(self):
        self._freq_group = FrequencyControls(self.controller)
        self.control_widgets.append(self._freq_group)
        return self._freq_group

    def _plot_controls(self):

        plot_group = QtGui.QGroupBox("Plot Control")
        self._plot_group = plot_group
        
        plot_controls_layout = QtGui.QVBoxLayout()
        
        first_row = QtGui.QHBoxLayout()
        marker_tab = QtGui.QTabBar()
        for marker in labels.MARKERS:
            marker_tab.addTab(marker)
        marker_tab.currentChanged.connect(lambda: cu._marker_tab_change(self))
        first_row.addWidget(marker_tab)
        
        self._marker_tab = marker_tab
        self.control_widgets.append(self._marker_tab)
        marker_check, marker_trace = self._marker_control()
        
        second_row = QtGui.QHBoxLayout()
        second_row.addWidget(marker_trace)
        second_row.addWidget(marker_check)
                
        third_row = QtGui.QHBoxLayout()
        third_row.addWidget(self._peak_control())
        third_row.addWidget(self._center_control())
        
        fourth_row = QtGui.QHBoxLayout()
        ref_level, ref_label, min_level, min_label = self._ref_controls()
        
        fourth_row.addWidget(ref_label)
        fourth_row.addWidget(ref_level)
        fourth_row.addWidget(min_label)
        fourth_row.addWidget(min_level)

        plot_controls_layout.addLayout(first_row)
        plot_controls_layout.addLayout(second_row)
        plot_controls_layout.addLayout(third_row)
        plot_controls_layout.addLayout(fourth_row)
        plot_group.setLayout(plot_controls_layout)
        
        return plot_group
        
    def _marker_control(self):
        marker_trace = QtGui.QComboBox()
        marker_trace.setEnabled(False)
        marker_trace.setMaximumWidth(50)
        marker_trace.currentIndexChanged.connect(lambda: cu._marker_trace_control(self))
        update_marker_traces(marker_trace, self._plot.traces)
        
        self._marker_trace = marker_trace
        marker_check = QtGui.QCheckBox('Enabled')
        marker_check.clicked.connect(lambda: cu._marker_control(self))
        self._marker_check = marker_check

        self.control_widgets.append(self._marker_check)
        return marker_check,marker_trace
            
    def _peak_control(self):
        peak = QtGui.QPushButton('Peak')
        peak.setToolTip("[P]\nFind peak of the selected spectrum") 
        peak.clicked.connect(lambda: cu._find_peak(self))
        self._peak = peak
        self.control_widgets.append(self._peak)
        return peak
                
    def _center_control(self):
        center = QtGui.QPushButton('Recenter')
        center.setToolTip("[C]\nCenter the Plot View around the available spectrum") 
        center.clicked.connect(lambda: cu._center_plot_view(self))
        self._center_bt = center
        self.control_widgets.append(self._center_bt)
        return center
    
    def _ref_controls(self):
        ref_level = QtGui.QLineEdit(str(PLOT_YMAX))
        ref_level.returnPressed.connect(lambda: cu._change_ref_level(self))
        self._ref_level = ref_level
        self.control_widgets.append(self._ref_level)
        ref_label = QtGui.QLabel('Reference Level: ')
        
        min_level = QtGui.QLineEdit(str(PLOT_YMIN)) 
        min_level.returnPressed.connect(lambda: cu._change_min_level(self))
        min_label = QtGui.QLabel('Minimum Level: ')
        self._min_level = min_level
        self.control_widgets.append(self._min_level)
        return ref_level, ref_label, min_level, min_label

    def _marker_labels(self):
        marker_label = QtGui.QLabel('')
        marker_label.setStyleSheet('color: %s;' % colors.TEAL)
        marker_label.setMinimumHeight(25)
        
        delta_label = QtGui.QLabel('')
        delta_label.setStyleSheet('color: %s;' % colors.TEAL)
        delta_label.setMinimumHeight(25)
        
        diff_label = QtGui.QLabel('')
        diff_label.setStyleSheet('color: %s;' % colors.WHITE)
        diff_label.setMinimumHeight(25)
        self._diff_lab = diff_label
        return marker_label,delta_label, diff_label

    def capture_received(self, state, fstart, fstop, raw, power, usable, segments):
        """
        :param state: SpecAState when capture was requested
        :param fstart: lowest frequency included in data in Hz
        :param fstop: highest frequency included in data in Hz
        :param raw: raw samples (None if not available)
        :param power: power spectrum
        :param usable: usable bins from power (None when sweeping)
        :param segments: bin segments from power (None when not sweeping)
        """
        self.raw_data = raw
        self.pow_data = power
        self.usable_bins = usable
        self.sweep_segments = segments

        xdata = np.linspace(fstart, fstop, len(power))

        self.update_trace(xdata)

        if self._plot_center_span != (state.center, state.span):
            self._plot_center_span = (state.center, state.span)
            self._plot.center_view(state.center, state.span)

        self.update_iq()
        self.update_marker()
        self.update_diff()

    def update_trace(self, xdata):

        #FIXME make alternate_colors user defined
        for trace in self._plot.traces:
            trace.update_curve(
                xdata,
                self.pow_data,
                self.usable_bins,
                self.sweep_segments,
                self.plot_state.alt_colors)


    def update_iq(self):
        if not self.raw_data:
            return

        if self.raw_data.stream_id == VRT_IFDATA_I14Q14:
            data = self.raw_data.data.numpy_array()
            i_data = np.array(data[:,0], dtype=float)/ZIF_BITS
            q_data = np.array(data[:,1], dtype=float)/ZIF_BITS
            self._plot.i_curve.setData(i_data)
            self._plot.q_curve.setData(q_data)
            self._plot.const_plot.clear()
            self._plot.const_plot.addPoints(
                x = i_data[0:CONST_POINTS],
                y = q_data[0:CONST_POINTS],
                symbol = 'o',
                size = 1, pen = 'y',
                brush = 'y')

        else:
            data = self.raw_data.data.numpy_array()
            i_data = np.array(data, dtype=float)

            if self.raw_data.stream_id == VRT_IFDATA_I14:
                i_data = i_data /ZIF_BITS

            elif self.raw_data.stream_id == VRT_IFDATA_I24:
                i_data = i_data / (np.mean(i_data)) - 1
            self._plot.i_curve.setData(i_data)

            self._plot.q_curve.clear()
            self._plot.const_plot.clear()

    def update_trig(self):
            if self.plot_state.trig_set:
                freq_region = self._plot.freqtrig_lines.getRegion()
                self.plot_state.trig_set = TriggerSettings(TRIGGER_TYPE_LEVEL,
                                                        min(freq_region), 
                                                        max(freq_region),
                                                        self._plot.amptrig_line.value())

                self.dut.trigger(self.plot_state.trig_set)
    def update_marker(self):        
            
            for marker, marker_label in zip(self._plot.markers, self.marker_labels):
                if marker.enabled:
                    trace = self._plot.traces[marker.trace_index]

                    if not trace.blank:
                        marker_label.setStyleSheet('color: rgb(%s, %s, %s);' % (trace.color[0],
                                                                             trace.color[1],
                                                                            trace.color[2]))
                        
                        marker.update_pos(trace.freq_range, trace.data)
                        marker_text = 'Frequency: %0.2f MHz \n Power %0.2f dBm' % (trace.freq_range[marker.data_index]/1e6, 
                                                                                   trace.data[marker.data_index])
                        marker_label.setText(marker_text)

                else:
                    marker_label.setText('')

    def update_diff(self):

        num_markers = 0
        traces = []
        data_indices = []
        for marker in self._plot.markers:

            if marker.enabled == True:
                num_markers += 1
                traces.append(self._plot.traces[marker.trace_index])
                data_indices.append(marker.data_index)
                
        if num_markers == len(labels.MARKERS):
            freq_diff = np.abs((traces[0].freq_range[data_indices[0]]/1e6) - (traces[1].freq_range[data_indices[1]]/1e6))
            
            power_diff = np.abs((traces[0].data[data_indices[0]]) - (traces[1].data[data_indices[1]]))
            
            delta_text = 'Delta : %0.1f MHz \nDelta %0.2f dBm' % (freq_diff, power_diff )
            self._diff_lab.setText(delta_text)
        else:
            self._diff_lab.setText('')

    def enable_controls(self):
        for item in self.control_widgets:
            item.setEnabled(True)
            
        
        for key in self._trace_attr:
            self._trace_attr[key].setEnabled(True)
        
    def disable_controls(self):
        for item in self.control_widgets:
            item.setEnabled(False)
            
        for key in self._trace_attr:
            self._trace_attr[key].setEnabled(False)

        
