from PyQt5.QtCore import QVariantAnimation, QEasingCurve
from PyQt5.QtWidgets import QProgressBar


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._target_value = 0
        self.setRange(0, 100)
        self.setTextVisible(False)

        self._anim = QVariantAnimation()
        self._anim.setDuration(500)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._update_value)


    def setValue(self, value):
        self._target_value = value
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(value)
        self._anim.start()


    def _update_value(self, val):
        super().setValue(int(val))