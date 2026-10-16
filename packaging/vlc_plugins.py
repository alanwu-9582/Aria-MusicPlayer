"""Which VLC plugins the packaged Aria leaves out (used by aria.spec).

Aria plays audio only (``--no-video``): local files of its own types (mp3, m4a, aac, flac, wav, ogg,
opus, webm, wma, mka) and http(s) / HLS / DASH streams, transcodes an excerpt to WAV for BPM analysis
(stream_out transcode + standard, access_output file, mux wav), reads a file's length (media_check)
and lists DirectSound outputs. Everything below is video, subtitles, discs, capture devices, remote
desktops, network *output*, add-ons or formats Aria never opens — checked with a probe that plays
every case and records the modules VLC loads (see packaging/SIZE_REPORT.md).

To ship every plugin again: ``PRUNE = False``.
"""

PRUNE = True

# whole plugin folders
FOLDERS = {
    "video_output", "video_filter", "video_chroma", "video_splitter", "visualization", "d3d9", "d3d11",
    "spu", "text_renderer", "services_discovery", "control", "lua", "stream_extractor",
}

# single plugins (file names) in the folders that stay
FILES = {
    # access: discs, capture devices, remote desktops and protocols Aria never opens
    "libaccess_srt_plugin.dll", "libvnc_plugin.dll", "libdcp_plugin.dll", "liblibbluray_plugin.dll",
    "libbluray-awt-j2se-1.3.2.jar", "libbluray-j2se-1.3.2.jar", "libdvdnav_plugin.dll", "libdvdread_plugin.dll",
    "libdshow_plugin.dll", "libdtv_plugin.dll", "libscreen_plugin.dll", "libsatip_plugin.dll", "libvcd_plugin.dll",
    "libvdr_plugin.dll", "libcdda_plugin.dll", "libsmb_plugin.dll", "libnfs_plugin.dll", "libsftp_plugin.dll",
    "libftp_plugin.dll", "librist_plugin.dll", "libshm_plugin.dll", "libaccess_wasapi_plugin.dll",
    "libtimecode_plugin.dll",
    # access_output: only "file" is used (the BPM excerpt)
    "libaccess_output_http_plugin.dll", "libaccess_output_livehttp_plugin.dll", "libaccess_output_rist_plugin.dll",
    "libaccess_output_shout_plugin.dll", "libaccess_output_srt_plugin.dll", "libaccess_output_udp_plugin.dll",
    # stream_out: only transcode + standard (and the small generic ones) are used
    "libstream_out_chromaprint_plugin.dll", "libstream_out_chromecast_plugin.dll",
    "libstream_out_mosaic_bridge_plugin.dll", "libstream_out_rtp_plugin.dll", "libstream_out_smem_plugin.dll",
    "libstream_out_display_plugin.dll", "libstream_out_bridge_plugin.dll", "libstream_out_cycle_plugin.dll",
    "libstream_out_gather_plugin.dll", "libstream_out_record_plugin.dll", "libstream_out_setid_plugin.dll",
    "libstream_out_stats_plugin.dll", "libstream_out_delay_plugin.dll", "libstream_out_description_plugin.dll",
    "libstream_out_autodel_plugin.dll",
    # codec: video, pictures, subtitles, hardware video decoding (all audio decoders stay, avcodec included)
    "libx265_plugin.dll", "libx264_plugin.dll", "libx26410b_plugin.dll", "libvpx_plugin.dll", "libaom_plugin.dll",
    "libdav1d_plugin.dll", "libschroedinger_plugin.dll", "libtheora_plugin.dll", "liblibmpeg2_plugin.dll",
    "liblibass_plugin.dll", "libzvbi_plugin.dll", "libkate_plugin.dll", "libdvbsub_plugin.dll",
    "libscte18_plugin.dll", "libscte27_plugin.dll", "libsubsdec_plugin.dll", "libsubstx3g_plugin.dll",
    "libsubsusf_plugin.dll", "libsvcdsub_plugin.dll", "libcvdsub_plugin.dll", "libspudec_plugin.dll",
    "libstl_plugin.dll", "libtextst_plugin.dll", "libttml_plugin.dll", "libwebvtt_plugin.dll", "libt140_plugin.dll",
    "libaribsub_plugin.dll", "liboggspots_plugin.dll", "libcrystalhd_plugin.dll", "libqsv_plugin.dll",
    "libdxva2_plugin.dll", "libd3d11va_plugin.dll", "libjpeg_plugin.dll", "libpng_plugin.dll",
    "libsdl_image_plugin.dll", "librawvideo_plugin.dll", "librtpvideo_plugin.dll", "libcdg_plugin.dll",
    "libcc_plugin.dll", "libfluidsynth_plugin.dll",
    # demux: video-only containers, subtitles, pictures, chiptune / MIDI (not Aria's file types)
    "libh26x_plugin.dll", "libmjpeg_plugin.dll", "libmpgv_plugin.dll", "libvc1_plugin.dll", "librawvid_plugin.dll",
    "librawdv_plugin.dll", "libdiracsys_plugin.dll", "libimage_plugin.dll", "libvobsub_plugin.dll",
    "libsubtitle_plugin.dll", "libdemux_stl_plugin.dll", "libdemux_cdg_plugin.dll", "libnuv_plugin.dll",
    "libty_plugin.dll", "libpva_plugin.dll", "libnsv_plugin.dll", "libdemux_chromecast_plugin.dll",
    "libdemuxdump_plugin.dll", "libsid_plugin.dll", "libgme_plugin.dll", "libmod_plugin.dll", "libsmf_plugin.dll",
    # packetizer: video
    "libpacketizer_av1_plugin.dll", "libpacketizer_dirac_plugin.dll", "libpacketizer_h264_plugin.dll",
    "libpacketizer_hevc_plugin.dll", "libpacketizer_mpeg4video_plugin.dll", "libpacketizer_mpegvideo_plugin.dll",
    "libpacketizer_vc1_plugin.dll",
    # misc: add-ons, scrobbling, fingerprinting, playlist export, RTSP serving
    "libaddonsfsstorage_plugin.dll", "libaddonsvorepository_plugin.dll", "libaudioscrobbler_plugin.dll",
    "libexport_plugin.dll", "libfingerprinter_plugin.dll", "libvod_rtsp_plugin.dll",
}


def keep(rel: str) -> bool:
    """``rel``: a path under the plugins folder ("codec/libx265_plugin.dll")."""
    if not PRUNE:
        return True
    parts = rel.replace("\\", "/").split("/")
    return not (parts[0] in FOLDERS or parts[-1] in FILES)
