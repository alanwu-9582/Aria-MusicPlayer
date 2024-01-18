import json
import os
import threading
import time
import random

import tkinter as tk
from tkinter import ttk, filedialog
from tkinter.messagebox import askyesno

import requests
from PIL import Image, ImageTk
from io import BytesIO

from Constants import *
from libs.MarqueeLabel import MarqueeLabel
from libs.LogTextbox import LogTextbox
from Player import Player
from YTLoader import YTLoader

class MusicPlayerGUI:
    def __init__(self, master):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.player = Player("prefer-insecure")
        self.loader = YTLoader()

        self.intitializeData()
        self.intitializeUI()
        
    def intitializeData(self):
        self.is_stopped = True

        with open(f'assets/queue.json', 'r', encoding='utf-8') as jfile:
            self.queue_data = json.load(jfile)['queue']
        self.queue_title = [data['title'] for data in self.queue_data]

        with open(f'assets/saved.json', 'r', encoding='utf-8') as jfile:
            self.saved_data = json.load(jfile)['saved']
        self.saved_title = [data['title'] for data in self.saved_data]

    def intitializeUI(self):
        self.master.geometry('745x350')
        self.master.title("Music Player")
        self.master.iconbitmap("assets/icon.ico")
        self.master.config(bg="#eeeeee", padx = 5, pady = 5)

        self.createFrames()
        self.createObjects()
        self.createFunctionButtons()
        self.applyStyle()
        # self.configureKeyBind()

    def createFrames(self):
        # Player Frame 上半部分
        self.player_frame = tk.Frame(self.master)
        self.player_frame.grid(row=0, column=0)
        self.player_frame.config(bg="#cccccc", padx=2, pady=5)

        self.player_left_frame = tk.Frame(self.player_frame)
        self.player_left_frame.grid(row=0, column=0)
        self.player_left_frame.config(bg="#cccccc", padx=5, pady=5)

        self.player_middle_frame = tk.Frame(self.player_frame)
        self.player_middle_frame.grid(row=0, column=1)
        self.player_middle_frame.config(bg="#cccccc", padx=2, pady=2)

        self.player_middle_info_frame = tk.Frame(self.player_middle_frame)
        self.player_middle_info_frame.grid(row=0, column=0)
        self.player_middle_info_frame.config(bg="#cccccc", padx=5, pady=5)

        self.player_middle_btn_frame = tk.Frame(self.player_middle_frame)
        self.player_middle_btn_frame.grid(row=1, column=0)
        self.player_middle_btn_frame.config(bg="#cccccc", padx=5, pady=5)

        self.player_right_frame = tk.Frame(self.player_frame)
        self.player_right_frame.grid(row=0, column=2)
        self.player_right_frame.config(bg="#cccccc", padx=5, pady=5)

        # Dashboard frame 下半部分
        self.dashboard_frame = tk.Frame(self.master)
        self.dashboard_frame.grid(row=1, column=0)
        self.dashboard_frame.config(bg="#eeeeee", padx=2, pady=5)

        self.dashboard_left_frame = tk.Frame(self.dashboard_frame)    
        self.dashboard_left_frame.grid(row=0, column=0)
        self.dashboard_left_frame.config(bg="#eeeeee", padx=5, pady=5)

        self.dashboard_left_data_frame = tk.Frame(self.dashboard_left_frame)    
        self.dashboard_left_data_frame.grid(row=0, column=0)
        self.dashboard_left_data_frame.config(bg="#eeeeee", padx=5, pady=5)

        self.dashboard_left_input_frame = tk.Frame(self.dashboard_left_frame)    
        self.dashboard_left_input_frame.grid(row=0, column=1)
        self.dashboard_left_input_frame.config(bg="#eeeeee", padx=5, pady=5)

        self.dashboard_left_btn_frame = tk.Frame(self.dashboard_left_input_frame)    
        self.dashboard_left_btn_frame.grid(row=3, column=0)
        self.dashboard_left_btn_frame.config(bg="#eeeeee", padx=5, pady=5)

        self.dashboard_right_frame = tk.Frame(self.dashboard_frame)    
        self.dashboard_right_frame.grid(row=0, column=1)
        self.dashboard_right_frame.config(bg="#eeeeee", padx=5, pady=5)

    def createObjects(self):
        # 音樂縮圖
        self.thumbnail_canvas = tk.Canvas(self.player_left_frame, width=THUMBNAILSIZE[0], height=THUMBNAILSIZE[1])
        self.thumbnail_canvas.grid(row=0, column=0, padx=3, pady=3)

        # 正在播放的音樂標題
        Label = tk.Label(self.player_middle_info_frame, text="正在播放: ", font=("Microsoft JhengHei UI", 12), anchor="w", width=7)
        Label.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)
        Label.grid(row=0, column=0)

        self.display_title = MarqueeLabel(self.player_middle_info_frame, text="", scroll_delay=250)
        self.display_title.config(font=("Microsoft JhengHei UI", 12), anchor="w", width=18)
        self.display_title.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)
        self.display_title.grid(row=0, column=1)

        # 正在播放的音樂作者
        Label = tk.Label(self.player_middle_info_frame, text="作者: ", font=("Microsoft JhengHei UI", 12), anchor="w", width=7)
        Label.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)
        Label.grid(row=1, column=0)

        self.display_author = tk.StringVar()
        Label = tk.Label(self.player_middle_info_frame, textvariable=self.display_author, text="", font=("Microsoft JhengHei UI", 12), anchor="w", width=18, bg="#cccccc")
        Label.grid(row=1, column=1)
        Label.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)

        # 音樂佇列顯示
        self.queue_strvar = tk.StringVar()
        listbox = tk.Listbox(self.player_right_frame,  listvariable=self.queue_strvar, font=("System", 10), height=8, relief='solid')
        listbox.grid(row=0, column=0, padx=3, pady=3)
        self.queue_strvar.set(self.queue_title)

        # 調整音量
        self.volume_slider = tk.Scale(self.player_right_frame, from_=100, to=0, orient='vertical', length=130, width=11, command=self.setVolume)
        self.volume_slider.grid(row=0, column=1) 
        self.volume_slider.set(60)

        # Console log
        self.log = LogTextbox(self.dashboard_left_data_frame, width=20,  height=8, font=("System", 10), relief="solid")
        self.log.grid(pady=5, columnspan=40)

        # 搜尋欄
        self.query_strvar = tk.StringVar()
        Entry = ttk.Entry(self.dashboard_left_input_frame,  textvariable=self.query_strvar, width=34)
        Entry.grid(row=0, column=0)

        # 搜尋結果
        self.select_combobox = ttk.Combobox(self.dashboard_left_input_frame, width=32)
        self.select_combobox.grid(row=1, column=0)

        # 儲存的音樂顯示
        self.saved_strvar = tk.StringVar()
        self.saved_listbox = tk.Listbox(self.dashboard_right_frame,  listvariable=self.saved_strvar, font=("System", 10), height=8, relief='solid', selectmode=tk.EXTENDED)
        self.saved_listbox.grid(row=0, column=0, padx=3, pady=3)
        self.saved_strvar.set(self.saved_title)
        
    def createFunctionButtons(self):
        DEAFULT_BUTTON_WIDTH = 10
        DEAFULT_PLAYER_BUTTON_WIDTH = 5

        # 停止播放
        self.stop_btn = ttk.Button(self.player_middle_btn_frame, text="停止", width=DEAFULT_PLAYER_BUTTON_WIDTH, command=self.stop)
        self.stop_btn.grid(row=0, column=0, padx=1, pady=2)

        # 播放與暫停
        self.play_btn = ttk.Button(self.player_middle_btn_frame, text="播放", width=DEAFULT_PLAYER_BUTTON_WIDTH, command=self.play)
        self.play_btn.grid(row=0, column=1, padx=1, pady=2)

        # 跳過正在播放的
        self.skip_btn = ttk.Button(self.player_middle_btn_frame, text="跳過", width=DEAFULT_PLAYER_BUTTON_WIDTH, command=self.skip)
        self.skip_btn.grid(row=0, column=2, padx=1, pady=2)

        # 打亂佇列
        self.shuffle_btn = ttk.Button(self.player_middle_btn_frame, text="隨機", width=DEAFULT_PLAYER_BUTTON_WIDTH, command=self.shuffle)
        self.shuffle_btn.grid(row=0, column=3, padx=1, pady=2)

        # 清空佇列
        self.clear_btn = ttk.Button(self.player_middle_btn_frame, text="清空", width=DEAFULT_PLAYER_BUTTON_WIDTH, command=self.clear)
        self.clear_btn.grid(row=0, column=4, padx=1, pady=2)

        # 搜尋 youtube
        self.search_btn = ttk.Button(self.dashboard_left_input_frame, text="搜尋", width=12, command=self.search)
        self.search_btn.grid(row=0, column=1, padx=1, pady=2)

        # 選擇搜尋結果
        self.select_btn = ttk.Button(self.dashboard_left_input_frame, text="選擇", width=12, command=self.select)
        self.select_btn.grid(row=1, column=1, padx=1, pady=2)

        # 把儲存的音樂加入佇列 (要先點選)
        self.addQueue_btn = ttk.Button(self.dashboard_left_btn_frame, text="加入佇列", width=DEAFULT_BUTTON_WIDTH, command=self.addQueue)
        self.addQueue_btn.grid(row=0, column=0, padx=1, pady=2)

        # 從儲存的音樂刪除 (要先點選)
        self.delete_btn = ttk.Button(self.dashboard_left_btn_frame, text="刪除", width=DEAFULT_BUTTON_WIDTH, command=self.delete)
        self.delete_btn.grid(row=0, column=1, padx=1, pady=2)

        # 把儲存的音樂下載至本地
        self.download_btn = ttk.Button(self.dashboard_left_btn_frame, text="下載", width=DEAFULT_BUTTON_WIDTH, command=self.download)
        self.download_btn.grid(row=0, column=2, padx=1, pady=2)

        # 重新讀取
        self.reload_btn = ttk.Button(self.dashboard_left_btn_frame, text="重新加載", width=DEAFULT_BUTTON_WIDTH, command=self.reload)
        self.reload_btn.grid(row=1, column=0, padx=1, pady=2)

        # 匯入歌單
        self.attachFile_import_btn = ttk.Button(self.dashboard_left_btn_frame, text="匯入歌單", width=DEAFULT_BUTTON_WIDTH, command=self.attachFileImport)
        self.attachFile_import_btn.grid(row=1, column=1, padx=1, pady=2)

        # 匯出歌單
        self.attachFile_export_btn = ttk.Button(self.dashboard_left_btn_frame, text="匯出歌單", width=DEAFULT_BUTTON_WIDTH, command=self.attachFileExport)
        self.attachFile_export_btn.grid(row=1, column=2, padx=1, pady=2)

    def applyStyle(self):
        self.master.config(bg=BACKGROUND_COLOR)

        self.player_frame.config(bg=PLAYER_BACKGROUND_COLOR)

        self.player_left_frame.config(bg=PLAYER_BACKGROUND_COLOR)
        self.player_middle_frame.config(bg=PLAYER_BACKGROUND_COLOR)
        self.player_middle_info_frame.config(bg=PLAYER_BACKGROUND_COLOR)
        self.player_middle_btn_frame.config(bg=PLAYER_BACKGROUND_COLOR)
        self.player_right_frame.config(bg=PLAYER_BACKGROUND_COLOR)

        self.dashboard_frame.config(bg=DASHBOARD_BACKGROUND_COLOR)

        self.dashboard_left_frame.config(bg=DASHBOARD_BACKGROUND_COLOR)
        self.dashboard_left_data_frame.config(bg=DASHBOARD_BACKGROUND_COLOR)
        self.dashboard_left_input_frame.config(bg=DASHBOARD_BACKGROUND_COLOR)
        self.dashboard_left_btn_frame.config(bg=DASHBOARD_BACKGROUND_COLOR)

        self.dashboard_right_frame.config(bg=DASHBOARD_BACKGROUND_COLOR)

        # 設置元件的背景色和前景色
        self.thumbnail_canvas.config(bg=FOREGROUND_COLOR)
        self.volume_slider.config(bg=PLAYER_BACKGROUND_COLOR, troughcolor=FOREGROUND_COLOR, fg=FOREGROUND_COLOR)
        self.log.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)
        self.saved_listbox.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)
        self.saved_listbox.config(bg=PLAYER_BACKGROUND_COLOR, fg=FOREGROUND_COLOR)

    def configureKeyBind(self):
        self.master.bind('<space>', self.test)

    def test(self, event):
        EVENTDICT = {
            'space': lambda: self.play()
        }

        EVENTDICT[event.keysym]()

    # 以下都不是 UI
    def playingChecker(self):
        def playingChecker_thread():
            while not self.player.is_playing():
                if self.is_stopped:
                    break

                time.sleep(0.1)
            
            while self.player.is_playing() or self.player.get_state() == 0:
                time.sleep(0.5)

            self.player.stop()
                
            if not self.is_stopped:
                self.play()
    
        tplaying_checker = threading.Thread(target=playingChecker_thread)
        tplaying_checker.start()

    def stop(self):
        self.player.stop()

        self.is_stopped = True

        self.display_title.update_text('')
        self.display_author.set('')
        self.updateThumbnail()

    def play(self):
        if self.player.get_state() == -1:
            if len(self.queue_data) <= 0:
                self.stop()
                return
            
            play_title = self.queue_title.pop(0)
            play_data = self.queue_data.pop(0)
            self.queue_strvar.set(self.queue_title)

            if play_data['saved_dist'] is None:
                if os.system('ping youtube.com -n 1'):
                    self.log.updateLog('Please check internet', LOG_ERROR_COLOR)
                    self.stop()
                    return
                
                play_path = play_data['url']
                
            else:
                play_path = play_data['saved_dist']
            
            self.display_title.update_text(f'{play_title}     ')
            self.display_author.set(play_data['author'])
            self.updateThumbnail(play_data['thumbnail_url'])
            
            self.player.play(play_path)
            self.is_stopped = False
            self.playingChecker()

        elif self.player.get_state() == 0:
            self.player.resume()

        elif self.player.get_state() == 1:
            self.player.pause()        
                
    def skip(self):
        self.player.stop()

    def shuffle(self):
        shuffled_queue_data = [None for i in range(len(self.queue_title))]
        shuffled_indexs = [i for i in range(len(self.queue_title))]
        random.shuffle(shuffled_indexs)
        for i, shuffled_index in enumerate(shuffled_indexs):
            shuffled_queue_data[shuffled_index] = self.queue_data[i]
             
        self.queue_data = shuffled_queue_data
        self.queue_title = [data['title'] for data in self.queue_data]
        self.queue_strvar.set(self.queue_title)

    def clear(self):
        self.queue_data = []
        self.queue_title = []
        self.queue_strvar.set(self.queue_title)

    def setVolume(self, event):
        self.player.set_volume(self.volume_slider.get())

    def search(self):
        query = self.query_strvar.get()
        def search_thread():
            self.search_btn.config(state=tk.DISABLED)
            self.log.updateLog(f'Search: {query}', LOG_PROCESS_COLOR)
            error = self.loader.search(query)
            if not error:
                search_reasult = self.loader.getResultsTitle()
                self.select_combobox.config(values=search_reasult)
                self.log.updateLog(f'Search: Find {len(search_reasult)} data', LOG_DONE_COLOR)

            else:
                self.log.updateLog(f'Error: {error}', LOG_ERROR_COLOR)
            self.search_btn.config(state=tk.NORMAL)
            
        tsearch = threading.Thread(target=search_thread)
        tsearch.start()

    def select(self):
        select_index = self.select_combobox.current()
        def select_thread():
            if select_index >= 0:
                self.loader.select(select_index)
                self.log.updateLog(f'Loading index: {select_index}', LOG_PROCESS_COLOR)
                youtube_info = self.loader.getYoutubeInfo()
                
                music_data = {
                    'title': self.loader.youtube.title,
                    'author': self.loader.youtube.author,
                    'watch_url': self.loader.youtube.watch_url,
                    'url': youtube_info['url'],
                    'thumbnail_url': youtube_info['thumbnail'],
                    'saved_dist': None
                }

                self.queue_data.append(music_data)
                self.queue_title.append(music_data['title'])

                if music_data['title'] not in self.saved_title:
                    self.saved_data.append(music_data)
                    self.saved_title.append(music_data['title'])

                else:
                    music_data['saved_dist'] = self.saved_data[self.saved_title.index(music_data['title'])]['saved_dist']
                    self.saved_data[self.saved_title.index(music_data['title'])] = music_data

                jdata = {'saved': self.saved_data}
                with open(f'assets/saved.json', 'w', encoding='utf-8') as jfile:
                    json.dump(jdata, jfile, ensure_ascii=False, indent=4)

                self.queue_strvar.set(self.queue_title)
                self.saved_strvar.set(self.saved_title)
                self.log.updateLog(f'Loaded index: {select_index}', LOG_DONE_COLOR)

        tselect = threading.Thread(target=select_thread)
        tselect.start()

    def addQueue(self):
        selections = self.saved_listbox.curselection()
        if len(selections) <= 0:
            return
    
        for selection in selections:
            self.queue_data.append(self.saved_data[selection])
            self.queue_title.append(self.saved_title[selection])

        self.queue_strvar.set(self.queue_title)

    def delete(self):
        selections = self.saved_listbox.curselection()
        if len(selections) <= 0:
            return
            
        if askyesno('Delete Saved', '確認刪除?'):
            for i, selection in enumerate(selections):
                deleted_title = self.saved_title.pop(selection-i)
                deleted_data = self.saved_data.pop(selection-i)
                if deleted_data['saved_dist'] is not None:
                    if deleted_title in self.queue_title:
                        self.queue_data[self.queue_title.index(deleted_title)]['saved_dist'] = None

                    os.remove(deleted_data['saved_dist'])

        self.saved_strvar.set(self.saved_title)
        jdata = {'saved': self.saved_data}
        with open(f'assets/saved.json', 'w', encoding='utf-8') as jfile:
            json.dump(jdata, jfile, ensure_ascii=False, indent=4)

    def download(self):
        selections = self.saved_listbox.curselection()
        if len(selections) <= 0:
            return
        
        def download_thread():
            for i, selection in enumerate(selections):
                self.log.updateLog(f'Downloading: {i+1}/{len(selections)}', LOG_PROCESS_COLOR)
                
                download_url = self.saved_data[selection]['watch_url']
                download_path = self.loader.download(download_url)
                if not download_path.startswith('Error'):
                    self.saved_data[selection]['saved_dist'] = download_path
                    if self.saved_title[selection] in self.queue_title:
                        self.queue_data[self.queue_title.index(self.saved_title[selection])]['saved_dist'] = download_path

            self.log.updateLog('Finished download', LOG_DONE_COLOR)

            jdata = {'saved': self.saved_data}
            with open(f'assets/saved.json', 'w', encoding='utf-8') as jfile:
                json.dump(jdata, jfile, ensure_ascii=False, indent=4)

        tdownload = threading.Thread(target=download_thread)
        tdownload.start()

    def reload(self):
        selections = self.saved_listbox.curselection()
        if len(selections) <= 0:
            return
    
        def reload_thread():
            for i, selection in enumerate(selections):
                self.log.updateLog(f'Reloading: {i+1}/{len(selections)}', LOG_PROCESS_COLOR)
                previous_saved_dist = self.saved_data[selection]['saved_dist']
                error = self.loader.search(self.saved_data[selection]['watch_url'])
                if not error:
                    self.log.updateLog(f'Getting index: {i+1} data', '#8BE5DF')
                    self.loader.select(0)
                    youtube_info = self.loader.getYoutubeInfo()

                    music_data = {
                        'title': self.loader.youtube.title,
                        'author': self.loader.youtube.author,
                        'watch_url': self.loader.youtube.watch_url,
                        'url': youtube_info['url'],
                        'thumbnail_url': youtube_info['thumbnail'],
                        'saved_dist': previous_saved_dist
                    }

                    self.saved_data[selection] = music_data
                    self.saved_title[selection] = music_data['title']
                    self.saved_strvar.set(self.saved_title)

                else:
                    self.log.updateLog(f'Error: {error}', LOG_ERROR_COLOR)
                    continue
            
            jdata = {'saved': self.saved_data}
            with open(f'assets/saved.json', 'w', encoding='utf-8') as jfile:
                json.dump(jdata, jfile, ensure_ascii=False, indent=4)

            self.log.updateLog('Finished reload', LOG_DONE_COLOR)

        treload = threading.Thread(target=reload_thread)
        treload.start()

    def attachFileImport(self):
        file_path = filedialog.askopenfilename(title="Import Playlist", filetypes= [("Json files","*.json")])
        if file_path:
            with open(file_path, 'r', encoding='utf-8') as jfile:
                self.saved_data = json.load(jfile)['saved']

            self.saved_title = [data['title'] for data in self.saved_data]

            audio_folder = 'assets\\audio'
            try:
                exists_audio = os.listdir(audio_folder)

            except FileNotFoundError:
                os.mkdir(audio_folder)
                exists_audio = os.listdir(audio_folder)
                
            data_dict = {data['title']: i for i, data in enumerate(self.saved_data)}
            for audio in exists_audio:
                for title in data_dict:
                    if audio.startswith(title):
                        self.saved_data[data_dict[title]]['saved_dist'] = f'{audio_folder}\\{audio}'
                        break

            jdata = {'saved': self.saved_data}
            with open(f'assets/saved.json', 'w', encoding='utf-8') as jfile:
                json.dump(jdata, jfile, ensure_ascii=False, indent=4)

            self.saved_strvar.set(self.saved_title)
            self.log.updateLog('Imported Playlist', LOG_DONE_COLOR)

    def attachFileExport(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("Json files", "*.json"), ("All files", "*.*")))
        if file_path:
            export_data = []
            for i, data in enumerate(self.saved_data):
                data['saved_dist'] = None
                export_data.append(data)

            jdata = {'saved': export_data}
            with open(file_path, 'w', encoding='utf-8') as jfile:
                json.dump(jdata, jfile, ensure_ascii=False, indent=4)

            self.log.updateLog('Exported Playlist', LOG_DONE_COLOR)

    def updateThumbnail(self, url=None):
        if url is None:
            self.thumbnail_canvas.delete(all)
            return
        
        def updateThumbnail_thread():
            try:
                response = requests.get(url)
                image = Image.open(BytesIO(response.content)).resize((THUMBNAILSIZE[0], THUMBNAILSIZE[1]))
                self.tk_img = ImageTk.PhotoImage(image)
                self.thumbnail_canvas.create_image(0, 0, image=self.tk_img, anchor='nw')

            except:
                self.thumbnail_canvas.delete(all)

        tupdate_thumbnail = threading.Thread(target=updateThumbnail_thread)
        tupdate_thumbnail.start()

    def on_closing(self):
        self.stop()

        jdata = {'queue': self.queue_data}
        with open(f'assets/queue.json', 'w', encoding='utf-8') as jfile:
            json.dump(jdata, jfile, ensure_ascii=False, indent=4)

        self.master.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    gui = MusicPlayerGUI(root)
    root.mainloop()