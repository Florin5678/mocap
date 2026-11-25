"""Results section (PROJECT.md §16.3): the quality report, export and deletion, and the 3D viewer. P6 rebuilds it
around trials; until then it shows the 0.1.x Results and Viewer pages as tabs."""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout

from mocapapp.gui.main_window import Section


class ResultsSection(Section):
    key, title = "results", "Results"
    purpose = "Quality report, export of the results, deletion of raw footage, and the 3D viewer."

    def __init__(self, win):
        super().__init__(win)
        from mocapapp.gui.pages.results import ResultsPage
        from mocapapp.gui.pages.viewer import ViewerPage

        self.results_page, self.viewer_page = ResultsPage(win), ViewerPage(win)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.results_page, "Report and export")
        self.tabs.addTab(self.viewer_page, "Viewer")
        self.tabs.currentChanged.connect(lambda _: self.tabs.currentWidget().refresh())
        for page in (self.results_page, self.viewer_page):
            page.completeChanged.connect(self.completeChanged)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def refresh(self) -> None:
        if self.session is not None:
            self.tabs.currentWidget().refresh()
