#!/usr/bin/env python

import os, sys, re
import subprocess
import json
import time
from math import ceil
from scripts.cmdhandling import exec_cmd
from scripts.getStats import calc_stats, get_segments

RESULTS="/results"
TMP="/tmpdir"
#TMP="testing"
TIMINGS="timings.json"

def run_ffmpeg_cmd(video, cmd, output=True):
    cmd = 'ffmpeg -i {video} {cmd}'.format(video=video, cmd=cmd)
    print(cmd)
    proc, stdout, stderr = exec_cmd(cmd, output=output)
    if 'error' in stdout.lower() or 'error' in stderr.lower():
        sys.exit('Failed to execute {cmd}'.format(cmd=cmd))
    return proc, stdout, stderr

def run_ffprobe_cmd(video, cmd, output=True):
    cmd = 'ffprobe -i {video} {cmd}'.format(video=video, cmd=cmd)
    #print(cmd)
    return exec_cmd(cmd, output=output)

def save_times(vid_opts,times):
    with open(vid_opts['times'], 'w') as fp:
        json.dump(times, fp)

def save_confs(vid_opts, vid_stats):
    with open(vid_opts['conf'], 'w') as fp:
        json.dump(vid_opts, fp)
    with open(vid_opts['vid_stats'], 'w') as fp:
        json.dump(vid_stats, fp)

def calc_get_stats(vid_opts, vid_stats):
    segments, durations = get_segments(vid_opts['m3u8'])
    stats = calc_stats(segments,vid_stats['fps'], durations)
    stats_clean = calc_stats(segments[:-1],vid_stats['fps'], durations)
    with open(vid_opts['stats'], 'w') as fp:
        json.dump(stats, fp)
    with open(vid_opts['stats_clean'], 'w') as fp:
        json.dump(stats_clean, fp)

def calc_ssim_psnr(vid_opts):
    cmd = '-nostats -threads 1 -i {vid_id} -lavfi \'ssim={ssim};[0:v][1:v]psnr={psnr}\' -f null - '.format(\
        vid_id = vid_opts['vid_id'], \
        ssim = vid_opts['ssim'], \
        psnr = vid_opts['psnr'] \
        )
    run_ffmpeg_cmd(vid_opts['m3u8'],cmd)
    
def extract_vid_stats(video):
    vid_stats = {}
    _, stdtout, stderr = run_ffprobe_cmd(video, \
        '-show_entries format=duration,bit_rate ' \
        '-show_entries stream=avg_frame_rate,height,width ' \
        '-print_format json -v quiet')
    json_dump = json.loads(stdtout)
    print('JSON: ', json_dump)
    vid_stats['bit_rate'] = float(json_dump['format']['bit_rate'])
    vid_stats['duration'] = float(json_dump['format']['duration'])
    tmp_fps = json_dump['streams'][0]['avg_frame_rate'].split('/')
    if 'k' in tmp_fps:
        vid_stats['fps'] = 1000
    else:
        vid_stats['fps'] = float(tmp_fps[0])/float(tmp_fps[1])
    vid_stats['resolution'] = '{w}x{h}'.format(w=json_dump['streams'][0]['width'], h=json_dump['streams'][0]['height'])
    return vid_stats

def encode_video_var(vid_opts,vid_stats):
    key_int_max = int(ceil(vid_opts['min_dur'] * vid_stats['fps']))
    key_int_min = int(ceil(vid_opts['max_dur'] * vid_stats['fps']))
    cmd = '-nostats -map 0:0 -threads 1 -vcodec lib{codec} '.format(codec=vid_opts['codec'])
    if 'key_frames_t' in vid_opts:
        # turnoff scenecut detection!
        cmd_split = '-{codec}-params keyint={key_int_max}:min-keyint={key_int_min}:scenecut=-1 -force_key_frames {key_frames_t} '.format(\
            codec=vid_opts['codec'],\
            key_int_max=key_int_max,\
            key_int_min=key_int_min,\
            key_frames_t=vid_opts['key_frames_t']\
        )
    else:
        cmd_split = '-{codec}-params keyint={key_int_max}:min-keyint={key_int_min} '.format(\
            codec=vid_opts['codec'],\
            key_int_max=key_int_max,\
            key_int_min=key_int_min \
            )
    cmd_split += \
        '-use_timeline 1 -use_template 1 -hls_playlist 1 -seg_duration 0 ' \
        ' -use_timeline 1 '

    if not 'cst_bitrate' in vid_opts:
        count = 0
        out_name = 'crf'
        if len(vid_opts['crf_vals']) > 1:
            for crf_val in vid_opts['crf_vals']:
                out_name += '_{crf_val}'.format(crf_val=crf_val)
                cmd += '-map 0:0 -crf:v:{count} {crf_val} ' \
                        .format(count=count, crf_val=crf_val)
                count += 1
        else:
            cmd += '-crf {crf_val} '.format(crf_val=vid_opts['crf_vals'][0])
    
    if 'cst_bitrate' in vid_opts:
        first_pass = '-pass {pass_nr} -b:v {cst_bitrate} '.format(\
            pass_nr=1,
            cst_bitrate=vid_opts['cst_bitrate']\
        )
        second_pass = '-pass {pass_nr} -b:v {cst_bitrate} '.format(\
            pass_nr=2,
            cst_bitrate=vid_opts['cst_bitrate']\
        )
        cmd_first = cmd + cmd_split + first_pass + ' -f null -'.format(\
            output = '/dev/null'
        )
        cmd_second = cmd + cmd_split + second_pass  + ' -f dash {output}'.format(\
            output = vid_opts['output']
        )
        print("Doing Firstpass")
        print(run_ffmpeg_cmd(vid_opts['vid_id'],cmd_first,output=True))
        print("Doing Secondpass")
        print(run_ffmpeg_cmd(vid_opts['vid_id'],cmd_second,output=True))
    else:
        cmd += cmd_split
        cmd += '-f dash {output}'.format(output=vid_opts['output'])
        print(run_ffmpeg_cmd(vid_opts['vid_id'],cmd,output=True))
    #print(cmd)

def encode_video_fixed(vid_opts,vid_stats):
    key_int_max = int(ceil(vid_opts['min_dur'] * vid_stats['fps']))
    key_int_min = int(ceil(vid_opts['max_dur'] * vid_stats['fps']))

    cmd = '-nostats -threads 1 -map 0:0 -vcodec lib{codec} '
    cmd = cmd.format(\
        codec=vid_opts['codec']\
    )
    cmd_split = '-{codec}-params keyint={key_int_max}:min-keyint={key_int_min} ' \
    '-use_timeline 0 -use_template 0 -hls_playlist 1 -seg_duration {target_seg_length} ' \
    '-force_key_frames \'expr:gte(t,n_forced*{target_seg_length})\' '\
    .format(\
        codec=vid_opts['codec'],\
        key_int_max=key_int_max,\
        key_int_min=key_int_min,\
        target_seg_length=vid_opts['target_seg_length']\
    )
    # Add First Pass (if CBR)
    if 'cst_bitrate' in vid_opts:
        first_pass = '-pass {pass_nr} -b:v {cst_bitrate} '.format(\
            pass_nr=1,
            cst_bitrate=vid_opts['cst_bitrate']\
        )
        second_pass = '-pass {pass_nr} -b:v {cst_bitrate} '.format(\
            pass_nr=2,
            cst_bitrate=vid_opts['cst_bitrate']\
        )
        cmd_first = cmd + cmd_split + first_pass + '-f null -'.format(\
            output = '/dev/null'
        )
        cmd_second = cmd + cmd_split + second_pass + '-f dash {output}'.format(\
            output = vid_opts['output']
            )
        print("Doing Firstpass")
        print(run_ffmpeg_cmd(vid_opts['vid_id'],cmd_first,output=True))
        print("Doing Secondpass", cmd_first)
        print(run_ffmpeg_cmd(vid_opts['vid_id'],cmd_second,output=True))
        return
    cmd += cmd_split
    cmd += '-crf {crf_val} '.format(\
        crf_val=vid_opts['crf_vals'][0]\
    )
    cmd += ' {output}'.format(\
        output=vid_opts['output']\
    )        
    print(run_ffmpeg_cmd(vid_opts['vid_id'],cmd,output=True))

def encode_video(vid_opts,vid_stats):
    if vid_opts['target_seg_length'] == 0:
        encode_video_var(vid_opts,vid_stats)
    else:
        encode_video_fixed(vid_opts,vid_stats)

def extract_vid_opts():
    vid_opts = {}
    if len(sys.argv) < 4:
        print('Usage: python video_encode.py ')
        exit()
    vid_opts['steady_id'] = str(sys.argv[1])
    vid_opts['vid_id'] = str('/videos/') + vid_opts['steady_id'] # vid_opts['steady_id']
    vid_opts['crf_vals'] = json.loads(str(sys.argv[2]))
    vid_opts['min_dur'] = float(sys.argv[3])
    vid_opts['max_dur'] = float(sys.argv[4])
    vid_opts['target_seg_length'] = float(sys.argv[5])
    vid_opts['codec'] = str(sys.argv[6])

    if len(sys.argv) > 7:
        if vid_opts['target_seg_length'] == 0.0:
            # TODO: Keyfreames have always to be there ...
            vid_opts['key_frames_t'] = str(sys.argv[7])
            if len(sys.argv) > 8:
                vid_opts['cst_bitrate'] = float(sys.argv[8])
        else:
            vid_opts['cst_bitrate'] = float(sys.argv[7])

    # TODO: Extract bitrate
    # vid_opts['const_bitrate'] = 0

    out_name = 'crf_{crfs}'.format(crfs=json.dumps(vid_opts['crf_vals'])\
            .replace(',','_')\
            .replace(' ', '')\
            .replace('[', '')\
            .replace(']', '')\
        )
    encoding_id='{steady_id}_{codec}_{crf_val}_{min_dur}_{max_dur}_{target_seg_length}'.format( \
        steady_id = vid_opts['steady_id'], \
        codec = vid_opts['codec'], \
        crf_val = out_name, \
        min_dur = str(vid_opts['min_dur']).replace('.','-'), \
        max_dur = str(vid_opts['max_dur']).replace('.','-'), \
        target_seg_length = str(vid_opts['target_seg_length']).replace('.','-') \
    )
    if 'cst_bitrate' in vid_opts:
        encoding_id += '_cbr_{cst_bitrate}'.format(cst_bitrate=vid_opts['cst_bitrate'])

    vid_opts['encoding_id'] = encoding_id
    
    # output paths
    if not os.path.exists('{TMP}'.format(TMP=TMP)): os.makedirs('{TMP}'.format(TMP=TMP))
    #output_dir = '{TMP}/{encoding_id}'.format(TMP=TMP,encoding_id=encoding_id)
    output_dir = '{TMP}'.format(TMP=TMP)
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    
    # this should go in some tmp folder...
    vid_opts['output'] = '{TMP}/{out_name}.mpd'.format(TMP=output_dir,out_name=out_name)
    vid_opts['m3u8'] = '{TMP}/media_0.m3u8'.format(TMP=output_dir)

    if not os.path.exists('{RESULTS}'.format(RESULTS=RESULTS)): os.makedirs('{RESULTS}'.format(RESULTS=RESULTS))
    #RESULTS_DIR = '{RESULTS}/{encoding_id}'.format(RESULTS=RESULTS,encoding_id=encoding_id)
    RESULTS_DIR = '{RESULTS}'.format(RESULTS=RESULTS)
    if not os.path.exists(RESULTS_DIR): os.makedirs(RESULTS_DIR)
    
    vid_opts['stats'] = '{RESULTS}/{out_name}'.format(RESULTS=RESULTS_DIR,out_name='video_statistics.json')
    vid_opts['stats_clean'] = '{RESULTS}/{out_name}'.format(RESULTS=RESULTS_DIR,out_name='video_statistics_clean.json')
    vid_opts['ssim'] = '{RESULTS}/ssim.log'.format(RESULTS=RESULTS_DIR)
    vid_opts['psnr'] = '{RESULTS}/psnr.log'.format(RESULTS=RESULTS_DIR)
    vid_opts['conf'] = '{RESULTS}/{out_name}'.format(RESULTS=RESULTS_DIR,out_name='vid_opts.json')
    vid_opts['vid_stats'] = '{RESULTS}/{out_name}'.format(RESULTS=RESULTS_DIR,out_name='vid_stats.json')
    vid_opts['times'] = '{RESULTS}/{out_name}'.format(RESULTS=RESULTS_DIR,out_name=TIMINGS)

    return vid_opts

if __name__== "__main__":
    times = {}
    vid_opts = extract_vid_opts()
    print(vid_opts)
    print('Extracting Video Stats')
    vid_stats = extract_vid_stats(vid_opts['vid_id'])
    print('Encode Video')
    enc_start = time.time()
    encode_video(vid_opts,vid_stats)
    times['enc_time'] = time.time() - enc_start
    print('Calculate PSNR and SSIM')
    calc_ssim_psnr_start = time.time()
    calc_ssim_psnr(vid_opts)
    times['calc_ssim_psnr_time'] = time.time() - calc_ssim_psnr_start
    print('Calculate Statistics')
    calc_stats_start = time.time()
    calc_get_stats(vid_opts,vid_stats)
    times['calc_stats_time'] = time.time() - calc_stats_start
    print('Save Configs')
    save_confs(vid_opts,vid_stats)
    save_times(vid_opts,times)
    print('Finished')