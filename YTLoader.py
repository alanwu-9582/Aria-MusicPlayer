from pytube import Search, YouTube
import yt_dlp
from Constants import *

class YTLoader:
    def __init__(self):
        self.results = None
        self.youtube = None

    def getResultsTitle(self):
        if self.results is None:
            return []
        
        else:
            results_title = []
            for result in self.results:
                results_title.append(result.title)

            return results_title
        
    def search(self, query):
        if query not in ["", " ", None]:
            try:
                search = Search(query)
                self.results = search.results

            except Exception as exception:
                return exception
            
        return

    def select(self, results_index):
        self.youtube = self.results[results_index]

    def getYoutubeInfo(self):
        try:
            ydl_options = YDL_OPTIONS['BestAudio']
            with yt_dlp.YoutubeDL(ydl_options) as ydl:
                video_info = ydl.extract_info(self.youtube.watch_url, download=False)
                self.youtube_information = video_info
                return self.youtube_information
            
        except Exception as exception:
            return exception
        
    def download(self, url):
        ydl_options = YDL_OPTIONS['BestAudio']

        try:
            with yt_dlp.YoutubeDL(ydl_options) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info_dict)
                return file_path

        except Exception as exception:
            return f'Error: {exception}'
        