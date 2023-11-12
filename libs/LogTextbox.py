import tkinter as tk

class LogTextbox(tk.Text):
    def __init__(self, master, log_limit=10, *args, **kwargs):
        tk.Text.__init__(self, master, *args, **kwargs)
        self.config(state=tk.DISABLED)
        self.log_count = 0
        self.log_limit = log_limit

    def updateLog(self, arg, color=None):
        self.config(state=tk.NORMAL)
        if self.log_count >= self.log_limit:
            self.delete("1.0", "end")
            self.log_count = 0

        self.insert("end", f"{arg}\n")
        self.log_count += 1

        if color:
            self.tag_add(arg, f'{self.log_count}.0', f'{self.log_count}.{len(arg)}')
            self.tag_config(arg, foreground=color)

        self.config(state=tk.DISABLED)