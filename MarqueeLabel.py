import tkinter as tk

class MarqueeLabel(tk.Label):
    def __init__(self, master, text, scroll_delay=100, *args, **kwargs):
        tk.Label.__init__(self, master, text=text, *args, **kwargs)
        self._scroll_delay = scroll_delay
        self._text = text
        self._marquee_task = None
        self.start_marquee()

    def start_marquee(self):
        self.config(text=self._text)
        self._marquee_task = self.after(self._scroll_delay, self._update_text)

    def _update_text(self):
        if len(self._text) > 0:
            self._text = self._text[1:] + self._text[0]  # 更新顯示的文字
            self.config(text=self._text)  # 更新 label 的顯示
        self._marquee_task = self.after(self._scroll_delay, self._update_text)

    def update_text(self, new_text):
        self._text = new_text
        self.config(text=self._text)

    def stop_marquee(self):
        if self._marquee_task:
            self.after_cancel(self._marquee_task)

