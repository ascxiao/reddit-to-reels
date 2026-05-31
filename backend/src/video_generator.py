"""
Video Generator for Reddit Story Maker.
Combines audio segments with background video and synchronized subtitles.

Author: Faheem Alvi
GitHub: https://github.com/FaheemAlvii
LinkedIn: https://www.linkedin.com/in/faheem-alvi
Email: faheemalvi2000@gmail.com
License: CC BY-NC 4.0
"""
import os
import sys
import random
import textwrap
from typing import List, Optional

# --- Graceful moviepy / PIL imports for A-Shell / iOS compatibility ---
MOVIEPY_AVAILABLE = False
try:
    import numpy as np
    import PIL.Image
    # Monkey patch ANTIALIAS replacement for MoviePy 1.0.3 compatibility with Pillow 10+
    if not hasattr(PIL.Image, 'ANTIALIAS'):
        PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip,
        concatenate_audioclips, TextClip, ColorClip, vfx, CompositeAudioClip
    )
    MOVIEPY_AVAILABLE = True
except ImportError:
    pass  # moviepy/numpy not available – FFmpeg-only mode

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

if getattr(sys, "frozen", False):
    PROJECT_ROOT = os.path.dirname(sys.executable)
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # If resolved inside backend/ directory, go up one more level to repository root
    if os.path.basename(PROJECT_ROOT) == "backend":
        PROJECT_ROOT = os.path.dirname(PROJECT_ROOT)

class VideoGenerator:
    """
    Generates videos from audio segments and background footage.
    """
    
    def __init__(self, mode: str = 'reel', use_gpu: bool = False, threads: int = 0, hw_accel: str = 'none'):
        """
        Initialize video generator.
        mode: 'reel' (9:16) or 'full' (16:9)
        use_gpu: Whether to use hardware encoding (legacy, overridden by hw_accel)
        threads: Number of threads for writing video (0 = auto/max)
        hw_accel: Hardware acceleration type: 'none' (CPU), 'nvenc' (NVIDIA), 'amf' (AMD)
        """
        self.mode = mode.lower()
        self.hw_accel = hw_accel if hw_accel in ('none', 'nvenc', 'amf') else ('nvenc' if use_gpu else 'none')
        self.use_gpu = self.hw_accel != 'none'
        self.threads = threads if threads and threads > 0 else os.cpu_count() or 4
        
        print(f"   ⚙️  Video Processor configured with {self.threads} threads.")
        
        if self.mode == 'reel' or self.mode == 'short_reel':
            self.width = 1080
            self.height = 1920
            self.aspect_ratio = 9/16
        else:
            self.width = 1920
            self.height = 1080
            self.aspect_ratio = 16/9
            
        self.backgrounds_dir = os.path.join(PROJECT_ROOT, "backgrounds")
        # Fall back to backend/backgrounds if running in Docker/volumes where it's mounted there
        if not os.path.exists(self.backgrounds_dir) or not any(f.lower().endswith(('.mp4', '.mov', '.avi')) for f in os.listdir(self.backgrounds_dir) if os.path.isfile(os.path.join(self.backgrounds_dir, f))):
            backend_bg = os.path.join(PROJECT_ROOT, "backend", "backgrounds")
            if os.path.exists(backend_bg) and any(f.lower().endswith(('.mp4', '.mov', '.avi')) for f in os.listdir(backend_bg) if os.path.isfile(os.path.join(backend_bg, f))):
                self.backgrounds_dir = backend_bg
            else:
                os.makedirs(self.backgrounds_dir, exist_ok=True)
        else:
            os.makedirs(self.backgrounds_dir, exist_ok=True)
            
        self.music_dir = os.path.join(PROJECT_ROOT, "music")
        if not os.path.exists(self.music_dir) or not any(f.lower().endswith(('.mp3', '.wav', '.m4a', '.aac')) for f in os.listdir(self.music_dir) if os.path.isfile(os.path.join(self.music_dir, f))):
            backend_music = os.path.join(PROJECT_ROOT, "backend", "music")
            if os.path.exists(backend_music) and any(f.lower().endswith(('.mp3', '.wav', '.m4a', '.aac')) for f in os.listdir(backend_music) if os.path.isfile(os.path.join(backend_music, f))):
                self.music_dir = backend_music
            else:
                os.makedirs(self.music_dir, exist_ok=True)
        else:
            os.makedirs(self.music_dir, exist_ok=True)
            
    def _load_font(self, font_name: str, size: int):
        """
        Load a font, trying a local impact.ttf first if requested, then Arial,
        then DejaVu Sans (standard in Linux/Docker), then fallback.
        """
        # Prioritize local impact.ttf if it contains 'impact'
        if "impact" in font_name.lower():
            candidates = [
                os.path.join(PROJECT_ROOT, "src", "impact.ttf"),
                os.path.join(PROJECT_ROOT, "impact.ttf"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "impact.ttf"),
                "impact.ttf"
            ]
            for c in candidates:
                if os.path.exists(c):
                    try:
                        return ImageFont.truetype(c, size)
                    except OSError:
                        pass

        # 1. Try to load requested font natively
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            pass

        # 2. Try DejaVu Sans (installed in our Docker container)
        is_bold = "bd" in font_name.lower() or "bold" in font_name.lower()
        linux_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if is_bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        
        if os.path.exists(linux_font):
            try:
                return ImageFont.truetype(linux_font, size)
            except OSError:
                pass
                
        # 3. Last fallback: try any local font we have
        local_impact = os.path.join(PROJECT_ROOT, "src", "impact.ttf")
        if os.path.exists(local_impact):
            try:
                return ImageFont.truetype(local_impact, size)
            except OSError:
                pass

        return ImageFont.load_default()

    def _wrap_text_by_pixels(self, text: str, font, max_width: int) -> List[str]:
        """
        Wrap text into lines so that no line exceeds max_width in pixels when rendered with the given font.
        """
        words = text.split()
        if not words:
            return []
            
        lines = []
        current_line = []
        dummy_img = Image.new('RGBA', (1, 1))
        draw = ImageDraw.Draw(dummy_img)
        
        for word in words:
            # Check width if we add this word to the current line
            test_line = " ".join(current_line + [word])
            # Use draw.textbbox to get the rendered width
            bbox = draw.textbbox((0, 0), test_line, font=font)
            width = bbox[2] - bbox[0]
            
            if width <= max_width or not current_line:
                current_line.append(word)
            else:
                lines.append(" ".join(current_line))
                current_line = [word]
                
        if current_line:
            lines.append(" ".join(current_line))
            
        return lines

    def create_text_image(self, text: str, fontsize: int = 60, color: str = 'white', 
                          bg_color: Optional[str] = None, max_width: int = 800,
                          use_bg_box: bool = False, bg_opacity: int = 255, padding: int = 40,
                          font_name: str = "arial.ttf") -> str:
        """
        Create an image with text using Pillow.
        Returns path to temporary image file.
        """
        # Try to load a nicer font (Arial/DejaVu) using helper, else default
        font = self._load_font(font_name, fontsize)
            
        # Wrap text by pixels instead of character counts
        lines = self._wrap_text_by_pixels(text, font, max_width)
        wrapped_text = "\n".join(lines)
        
        # Calculate size
        dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        bbox = dummy_draw.multiline_textbbox((0, 0), wrapped_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        img_width = text_width + padding * 2
        img_height = text_height + padding * 2
        
        # Create image
        if use_bg_box and bg_color:
            img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            box_color = None
            if bg_color.startswith('#'):
                h = bg_color.lstrip('#')
                rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
                box_color = rgb + (bg_opacity,)
            else:
                if bg_color.lower() == 'black':
                    box_color = (0, 0, 0, bg_opacity)
                elif bg_color.lower() == 'white':
                    box_color = (255, 255, 255, bg_opacity)
                else: 
                    box_color = (0, 0, 0, bg_opacity)
            
            draw.rounded_rectangle(
                [(0, 0), (img_width, img_height)], 
                radius=20, 
                fill=box_color
            )
            
        elif bg_color:
             img = Image.new('RGBA', (img_width, img_height), bg_color)
             draw = ImageDraw.Draw(img)
        else:
            img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
        # For outline style (no background box), use a thick black stroke dynamically sized to the font
        if not use_bg_box:
             stroke_width = max(3, int(fontsize * 0.08))
             stroke_color = 'black'
        else:
             stroke_width = 0
             stroke_color = 'black'
        
        draw.multiline_text(
            (padding, padding), 
            wrapped_text, 
            font=font, 
            fill=color, 
            align='center',
            stroke_width=stroke_width,
            stroke_fill=stroke_color
        )
        
        # Save temp file
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        img.save(temp_file.name)
        temp_file.close()
        
        return temp_file.name

    def get_random_background(self, duration: float) -> VideoFileClip:
        """
        Get a random background clip of appropriate duration and aspect ratio.
        """
        video_files = [f for f in os.listdir(self.backgrounds_dir) 
                      if f.lower().endswith(('.mp4', '.mov', '.avi'))]
        
        if not video_files:
            # Create a simple color background if no videos found
            print("⚠️  No background videos found in 'backgrounds/'. Using solid color.")
            return ColorClip(size=(self.width, self.height), color=(20, 20, 30), duration=duration)
            
        bg_path = os.path.join(self.backgrounds_dir, random.choice(video_files))
        
        try:
            clip = VideoFileClip(bg_path)
            
            # Loop if shorter than duration
            if clip.duration < duration:
                clip = clip.loop(duration=duration)
            
            # Pick random start time if long enough
            if clip.duration > duration:
                max_start = clip.duration - duration
                start_time = random.uniform(0, max_start)
                clip = clip.subclip(start_time, start_time + duration)
            
            # Resize logic (Crop to fill)
            # Calculate target aspect ratio
            target_aspect = self.width / self.height
            clip_aspect = clip.w / clip.h
            
            if clip_aspect > target_aspect:
                # Clip is wider than target -> Resize by height, crop width
                new_height = self.height
                new_width = int(clip.w * (self.height / clip.h))
                clip = clip.resize(height=new_height)
                # Center crop width
                x_center = new_width / 2
                clip = clip.crop(x1=x_center - self.width/2, width=self.width, height=self.height)
            else:
                # Clip is taller than target -> Resize by width, crop height
                new_width = self.width
                new_height = int(clip.h * (self.width / clip.w))
                clip = clip.resize(width=new_width)
                # Center crop height
                y_center = new_height / 2
                clip = clip.crop(y1=y_center - self.height/2, width=self.width, height=self.height)
                
            return clip
            
        except Exception as e:
            print(f"❌ Error loading background {bg_path}: {e}")
            return ColorClip(size=(self.width, self.height), color=(20, 20, 30), duration=duration)

    def generate_video(self, audio_segments: List[dict], output_path: str, tail_text: Optional[str] = None, tail_duration: float = 0.0, branding: str = "", music_file: Optional[str] = None, music_volume: float = 0.1):
        """
        Generate final video from audio segments.
        audio_segments: List of dicts {'text': str, 'audio_path': str, 'author': str (opt)}
        """
        print(f"\n🎬 Generatiing video ({self.mode.upper()} mode)...")
        
        # Resolve thumbnail path for title overlay
        thumbnail_path = None
        post_dir = None
        for segment in audio_segments:
            ap = segment.get('audio_path', '')
            if ap:
                p = os.path.dirname(os.path.dirname(ap))
                if os.path.isdir(p):
                    post_dir = p
                    break
        
        # Determine part number
        part_number = 1
        import re
        match = re.search(r'part(\d+)', os.path.basename(output_path), re.IGNORECASE)
        if match:
            part_number = int(match.group(1))
            
        if post_dir:
            candidates = [
                os.path.join(post_dir, f"thumbnail_part{part_number}.png"),
                os.path.join(post_dir, f"video_part{part_number}_thumbnail.png"),
                os.path.join(post_dir, "thumbnail.png")
            ]
            for c in candidates:
                if os.path.exists(c):
                    thumbnail_path = c
                    break
                    
        # Fallback dynamic generation if not found
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            print(f"   ⚠️  Thumbnail not pre-generated. Creating dynamic fallback...")
            import tempfile
            fallback_path = os.path.join(post_dir or tempfile.gettempdir(), f"temp_thumb_part{part_number}.png")
            p_title = "Reddit Story"
            p_sub = "AskReddit"
            p_score = 0
            if post_dir and os.path.exists(os.path.join(post_dir, "summary.json")):
                try:
                    with open(os.path.join(post_dir, "summary.json"), 'r', encoding='utf-8') as sf:
                        s_data = json.load(sf)
                        p_title = s_data.get('title', p_title)
                        p_sub = s_data.get('subreddit', p_sub)
                        p_score = s_data.get('score', p_score)
                except:
                    pass
            thumbnail_path = self.generate_thumbnail(
                title=p_title,
                subreddit=p_sub,
                part_number=part_number,
                total_parts=1,  # fallback
                output_path=fallback_path,
                score=p_score,
                branding=branding
            )
            
        # Distribute thumbnail_path to all title segments and detect title/credits
        for idx, segment in enumerate(audio_segments):
            # If the filename itself contains 'credits', tag it as credits
            if 'credits' in os.path.basename(segment.get('audio_path', '')).lower():
                segment['is_credits'] = True
            # If it's the very first segment and not yet tagged, or if it's the title segment
            if idx == 0 and not any(s.get('is_title') for s in audio_segments):
                segment['is_title'] = True
                
            if segment.get('is_title'):
                # Only display the full-frame thumbnail card at the start of Part 1
                segment['thumbnail_path'] = thumbnail_path if part_number == 1 else None
 
        # 1. Prepare Audio
        audio_clips = []
        temp_images = [] # Keep track to delete later
        
        for segment in audio_segments:
            if os.path.exists(segment['audio_path']):
                ac = AudioFileClip(segment['audio_path'])
                audio_clips.append(ac)
            else:
                print(f"⚠️  Missing audio file: {segment['audio_path']}")
        
        if not audio_clips:
            print("❌ No valid audio clips found")
            return None
            
        final_audio = concatenate_audioclips(audio_clips)
        total_duration = final_audio.duration + (tail_duration if (tail_text and tail_duration and tail_duration > 0) else 0)

        # Mix background music if enabled
        if music_file and music_file != "none":
            try:
                music_path = os.path.join(self.music_dir, music_file)
                if music_file.lower() == "random":
                    music_files = [f for f in os.listdir(self.music_dir) if f.lower().endswith(('.mp3', '.wav', '.m4a', '.aac'))]
                    if music_files:
                        music_path = os.path.join(self.music_dir, random.choice(music_files))
                    else:
                        music_path = None
                        print("⚠️ No music files found in 'music/' directory.")
                
                if music_path and os.path.exists(music_path):
                    print(f"   🎵 Adding background music: {os.path.basename(music_path)} at volume {music_volume}")
                    music_clip = AudioFileClip(music_path)
                    
                    if music_clip.duration < total_duration:
                        music_clip = music_clip.loop(duration=total_duration)
                    else:
                        music_clip = music_clip.subclip(0, total_duration)
                    
                    music_clip = music_clip.volumex(music_volume)
                    final_audio = CompositeAudioClip([final_audio, music_clip])
            except Exception as e:
                print(f"⚠️ Error mixing background music: {e}")
        
        # 2. Prepare Background
        background_clip = self.get_random_background(total_duration)
        
        # 3. Create Subtitles & Attribution
        subtitle_clips = []
        attribution_clips = []
        current_time = 0
        
        total_segments = len(audio_segments)
        print(f"   Composing {total_segments} segments...")
        
        for i, segment in enumerate(audio_segments):
            # Print progress every 10 segments or for first/last
            if i % 10 == 0 or i == total_segments - 1:
                print(f"     Processing segment {i+1}/{total_segments}...")
            segment_duration = audio_clips[i].duration
            
            # Title segment gets the full-frame card thumbnail overlay
            if segment.get('is_title') and segment.get('thumbnail_path') and os.path.exists(segment['thumbnail_path']):
                txt_clip = (ImageClip(segment['thumbnail_path'])
                           .set_start(current_time)
                           .set_duration(segment_duration)
                           .set_position(('center', 'center')))
                subtitle_clips.append(txt_clip)
                current_time += segment_duration
                continue

            # Credits segment gets a stylized posted by u/[author] card at 80px
            if segment.get('is_credits'):
                text_img_path = self.create_text_image(
                    segment['text'], 
                    fontsize=80,
                    color='white',
                    max_width=int(self.width * 0.8),
                    use_bg_box=True,
                    bg_color='black',
                    bg_opacity=160,
                    padding=30,
                    font_name="impact.ttf"
                )
                temp_images.append(text_img_path)
                
                txt_clip = (ImageClip(text_img_path)
                           .set_start(current_time)
                           .set_duration(segment_duration)
                           .set_position(('center', 'center')))
                subtitle_clips.append(txt_clip)
                current_time += segment_duration
                continue

            # Regular segment gets rapid popup (grouped to 3-4 words for optimal readability)
            words = segment['text'].split()
            word_groups = []
            group_size = 3 if len(words) > 4 else len(words)
            if group_size == 0:
                current_time += segment_duration
                continue
                
            for w_idx in range(0, len(words), group_size):
                word_groups.append(" ".join(words[w_idx:w_idx+group_size]))
                
            # Distribute segment duration proportionally to length of word groups
            total_chars = sum(len(g) for g in word_groups)
            if total_chars > 0:
                durations = [segment_duration * (len(g) / total_chars) for g in word_groups]
            else:
                durations = [segment_duration / len(word_groups)] * len(word_groups)
                
            sub_start = current_time
            for group, dur in zip(word_groups, durations):
                text_img_path = self.create_text_image(
                    group,
                    fontsize=120 if self.mode == 'reel' or self.mode == 'short_reel' else 80,
                    color='white',
                    max_width=int(self.width * 0.85),
                    use_bg_box=False,
                    font_name="impact.ttf"
                )
                temp_images.append(text_img_path)
                
                txt_clip = (ImageClip(text_img_path)
                           .set_start(sub_start)
                           .set_duration(dur)
                           .set_position(('center', 'center')))
                subtitle_clips.append(txt_clip)
                sub_start += dur
                
            current_time += segment_duration

        if tail_text and tail_duration and tail_duration > 0:
            tail_img_path = self.create_text_image(
                tail_text,
                fontsize=70 if self.mode == 'reel' else 50,
                color='white',
                max_width=int(self.width * 0.8),
                use_bg_box=True,
                bg_color='black',
                bg_opacity=160,
                padding=40
            )
            temp_images.append(tail_img_path)
            tail_clip = (ImageClip(tail_img_path)
                        .set_start(current_time)
                        .set_duration(tail_duration)
                        .set_position(('center', 'center')))
            subtitle_clips.append(tail_clip)
        
        # 4. Branding watermark (persistent overlay)
        branding_clips = []
        if branding and branding.strip():
            brand_img_path = self.create_text_image(
                branding.strip(),
                fontsize=30,
                color='white',
                max_width=int(self.width * 0.4),
                use_bg_box=True,
                bg_color='black',
                bg_opacity=120,
                padding=12
            )
            temp_images.append(brand_img_path)
            brand_clip = (ImageClip(brand_img_path)
                         .set_duration(total_duration)
                         .set_position(('right', 'bottom'))
                         .margin(right=20, bottom=20, opacity=0))
            branding_clips.append(brand_clip)

        # 5. Composite
        final_video = CompositeVideoClip([background_clip] + subtitle_clips + attribution_clips + branding_clips)
        final_video = final_video.set_audio(final_audio)
        
        # 5. Write file
        print(f"   Writing video to: {output_path}")
        try:
            # Use unique temp audio filename in the output directory
            output_dir = os.path.dirname(output_path)
            temp_audio = os.path.join(output_dir, f"temp_audio_{random.randint(1000, 9999)}.m4a")
            
            print(f"   Writing video to: {output_path}")
            
            # Codec settings based on hw_accel
            if self.hw_accel == 'nvenc':
                print("   🚀 Using NVIDIA GPU acceleration (h264_nvenc)...")
                codec = 'h264_nvenc'
                ffmpeg_params = ['-rc', 'vbr', '-cq', '19', '-b:v', '8M', '-maxrate', '10M']
                preset = 'p4'
                bitrate = None
            elif self.hw_accel == 'amf':
                print("   🚀 Using AMD GPU acceleration (h264_amf)...")
                codec = 'h264_amf'
                ffmpeg_params = ['-rc', 'vbr_latency', '-qp_i', '19', '-qp_p', '19', '-b:v', '8M', '-maxrate', '10M']
                preset = 'speed'
                bitrate = None
            else:
                print("   Using CPU encoding (libx264)...")
                codec = 'libx264'
                ffmpeg_params = ['-crf', '18']
                preset = 'medium'
                bitrate = None
            
            final_video.write_videofile(
                output_path, 
                fps=30, # Smoother 30fps
                codec=codec, 
                audio_codec='aac',
                bitrate=bitrate,
                ffmpeg_params=ffmpeg_params,
                preset=preset,
                threads=self.threads,       # Use configured threads
                logger='bar',    # Show progress bar
                temp_audiofile=temp_audio,
                remove_temp=True
            )
            print("✓ Video generation complete!")
            
            # clean up temp images
            print("   Cleaning up temporary files...")
            for p in temp_images:
                try:
                    os.remove(p)
                except:
                    pass
            
            # Explicitly close clips to release file handles
            final_video.close()
            final_audio.close()
            background_clip.close()
            for ac in audio_clips:
                ac.close()
                    
            return output_path
            
        except Exception as e:
            print(f"❌ Error writing video: {e}")
            return None

    def create_full_frame_overlay(self, segment: dict, current_author: str, branding: str = "") -> str:
        """
        Create a single full-frame transparent PNG containing both subtitle and attribution.
        This is for the FFmpeg engine to overlay cleanly as a stream.
        """
        # If title segment and pre-generated thumbnail exists, copy it directly
        if segment.get('is_title') and segment.get('thumbnail_path') and os.path.exists(segment['thumbnail_path']):
            import shutil
            import tempfile
            temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            temp_file.close()
            shutil.copy2(segment['thumbnail_path'], temp_file.name)
            return temp_file.name
            
        # Determine styling parameters
        if segment.get('is_credits'):
            fontsize = 80
            use_bg_box = True
            font_name = "impact.ttf"
            max_w_ratio = 0.8
            padding = 30
        else:
            fontsize = segment.get('fontsize', 120 if self.mode == 'reel' or self.mode == 'short_reel' else 80)
            use_bg_box = segment.get('use_bg_box', False)
            font_name = segment.get('font_name', "impact.ttf")
            max_w_ratio = 0.85 if not use_bg_box else 0.8
            padding = 40

        # 1. Create Subtitle Image
        text_img_path = self.create_text_image(
            segment['text'], 
            fontsize=fontsize,
            color='white',
            max_width=int(self.width * max_w_ratio),
            use_bg_box=use_bg_box,
            bg_color='black',
            bg_opacity=160,
            padding=padding,
            font_name=font_name
        )
        
        # 2. Create Base Canvas
        canvas = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
        
        # 3. Paste Subtitle (Center)
        sub_img = Image.open(text_img_path).convert("RGBA")
        sub_w, sub_h = sub_img.size
        sub_x = (self.width - sub_w) // 2
        sub_y = (self.height - sub_h) // 2
        canvas.alpha_composite(sub_img, (sub_x, sub_y))
        
        # 4. Create Attribution Image — skip for credits or outline style subtitles to keep screen clean
        include_attr = bool(branding and branding.strip() and use_bg_box and not segment.get('is_credits'))
        if include_attr:
            attr_text = f"u/{branding.strip()}"
            attr_img_path = self.create_text_image(
                attr_text,
                fontsize=40 if self.mode == 'reel' else 30,
                color='#FF4500',
                max_width=int(self.width * 0.5),
                use_bg_box=True,
                bg_color='black',
                bg_opacity=160,
                padding=15
            )
        
        # 5. Paste Attribution (Top-Left relative to subtitle)
        if include_attr:
            attr_img = Image.open(attr_img_path).convert("RGBA")
            attr_w, attr_h = attr_img.size
            attr_x = sub_x
            attr_y = sub_y - attr_h - 10
            canvas.alpha_composite(attr_img, (attr_x, attr_y))
        
        # 6. Branding watermark (bottom-right) — only overlay if not title slide to prevent duplicate brand cards
        if branding and branding.strip() and not segment.get('is_title'):
            brand_img_path = self.create_text_image(
                branding.strip(),
                fontsize=30,
                color='white',
                max_width=int(self.width * 0.4),
                use_bg_box=True,
                bg_color='black',
                bg_opacity=120,
                padding=12
            )
            brand_img = Image.open(brand_img_path).convert("RGBA")
            bw, bh = brand_img.size
            canvas.alpha_composite(brand_img, (self.width - bw - 20, self.height - bh - 20))
            try:
                os.remove(brand_img_path)
            except:
                pass

        # Save Full Frame
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        canvas.save(temp_file.name)
        temp_file.close()
        
        # Cleanup small parts
        try:
            os.remove(text_img_path)
            if include_attr:
                os.remove(attr_img_path)
        except:
            pass
            
        return temp_file.name

    def generate_thumbnail(self, title: str, subreddit: str, part_number: int = 1,
                           total_parts: int = 1, output_path: str = "thumbnail.png",
                           score: int = 0, branding: str = "", title_override: str = None) -> Optional[str]:
        """
        Generate a Reddit-style thumbnail for a video part.
        Card size adapts to content. Includes optional branding watermark.
        """
        print(f"   🖼️  Generating thumbnail for Part {part_number}...")
        try:
            w, h = self.width, self.height

            # 1. Background — grab a frame from a random background video or use solid
            bg_img = None
            video_files = [f for f in os.listdir(self.backgrounds_dir)
                          if f.lower().endswith(('.mp4', '.mov', '.avi'))]
            if video_files:
                bg_path = os.path.join(self.backgrounds_dir, random.choice(video_files))
                try:
                    clip = VideoFileClip(bg_path)
                    t = random.uniform(0, max(clip.duration - 1, 0))
                    frame = clip.get_frame(t)
                    clip.close()
                    bg_img = Image.fromarray(frame).resize((w, h), Image.LANCZOS)
                    from PIL import ImageFilter
                    bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=12))
                    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 80))
                    bg_img = bg_img.convert('RGBA')
                    bg_img = Image.alpha_composite(bg_img, overlay)
                except Exception as e:
                    print(f"   ⚠️  Could not extract background frame: {e}")

            if bg_img is None:
                bg_img = Image.new('RGBA', (w, h), (20, 20, 30, 255))

            draw = ImageDraw.Draw(bg_img)

            # 2. Load fonts using robust loader (supports Linux/Docker DejaVu and Windows/macOS Arial)
            font_title = self._load_font("arialbd.ttf", 52)
            font_sub = self._load_font("arial.ttf", 36)
            font_meta = self._load_font("arial.ttf", 30)
            font_brand = self._load_font("arial.ttf", 26)

            # 3. Measure content to determine dynamic card height
            card_margin_x = int(w * 0.08)
            card_w = w - card_margin_x * 2
            inner_pad = 30
            title_max_w = card_w - inner_pad * 2

            # Subreddit header height
            icon_r = 24
            header_h = icon_r * 2 + 10  # icon + gap

            # Title text measurement using robust pixel wrapping
            display_title = title_override if title_override else title
            if total_parts > 1 and not title_override:
                display_title = f"{title} (Part {part_number})"
            elif total_parts > 1 and title_override:
                display_title = f"{title_override} (Part {part_number})"
            
            lines = self._wrap_text_by_pixels(display_title, font_title, title_max_w)
            max_lines = 6
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                lines[-1] = lines[-1] + "..."
            wrapped = "\n".join(lines)
            title_bbox = draw.multiline_textbbox((0, 0), wrapped, font=font_title, spacing=8)
            title_text_h = title_bbox[3] - title_bbox[1]

            # Bottom bar height
            bottom_bar_h = 40

            # Calculate total card height dynamically
            card_h = inner_pad + header_h + 20 + title_text_h + 25 + bottom_bar_h + inner_pad
            card_h = max(card_h, int(h * 0.18))  # minimum height
            card_h = min(card_h, int(h * 0.55))  # maximum height

            card_y = (h - card_h) // 2
            card_x = card_margin_x

            # 4. Draw rounded white card
            card_rect = [(card_x, card_y), (card_x + card_w, card_y + card_h)]
            draw.rounded_rectangle(card_rect, radius=30, fill=(255, 255, 255, 240))

            # 5. Reddit icon circle + subreddit name
            icon_y = card_y + inner_pad
            icon_x = card_x + inner_pad
            draw.ellipse(
                [(icon_x, icon_y), (icon_x + icon_r * 2, icon_y + icon_r * 2)],
                fill=(255, 69, 0)
            )
            cx, cy = icon_x + icon_r, icon_y + icon_r
            draw.ellipse([(cx - 8, cy - 6), (cx - 2, cy)], fill='white')
            draw.ellipse([(cx + 2, cy - 6), (cx + 8, cy)], fill='white')
            draw.arc([(cx - 8, cy - 2), (cx + 8, cy + 8)], 0, 180, fill='white', width=2)

            # Show branding handle instead of subreddit for privacy
            sub_text = f"u/{branding.strip()}" if branding and branding.strip() else f"r/{subreddit}"
            draw.text((icon_x + icon_r * 2 + 12, icon_y + 8), sub_text, fill=(30, 30, 30), font=font_sub)

            # 6. Part badge (top right of card)
            if total_parts > 1:
                badge_text = f"Part {part_number}/{total_parts}"
                badge_bbox = draw.textbbox((0, 0), badge_text, font=font_sub)
                badge_w = badge_bbox[2] - badge_bbox[0] + 30
                badge_h = badge_bbox[3] - badge_bbox[1] + 16
                badge_x = card_x + card_w - badge_w - 20
                badge_y_pos = card_y + inner_pad
                draw.rounded_rectangle(
                    [(badge_x, badge_y_pos), (badge_x + badge_w, badge_y_pos + badge_h)],
                    radius=badge_h // 2, fill=(255, 69, 0)
                )
                draw.text((badge_x + 15, badge_y_pos + 5), badge_text, fill='white', font=font_sub)

            # 7. Title text (centered in remaining space)
            title_y = icon_y + icon_r * 2 + 20
            draw.multiline_text(
                (card_x + inner_pad, title_y), wrapped,
                fill=(20, 20, 20), font=font_title, spacing=8
            )

            # 8. Bottom bar — hearts + share count
            bottom_y = card_y + card_h - inner_pad - 25
            heart = "♡"
            score_text = f"{score:,}+" if score else "999+"
            share_text = f"⤴ {score_text}"
            draw.text((card_x + inner_pad, bottom_y), f"{heart} {score_text}", fill=(120, 120, 120), font=font_meta)
            draw.text((card_x + card_w - 180, bottom_y), share_text, fill=(120, 120, 120), font=font_meta)

            # 9. Branding watermark (bottom-right corner of image)
            if branding and branding.strip():
                brand_text = branding.strip()
                brand_bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
                brand_tw = brand_bbox[2] - brand_bbox[0]
                brand_th = brand_bbox[3] - brand_bbox[1]
                brand_pad = 12
                brand_x = w - brand_tw - brand_pad - 20
                brand_y = h - brand_th - brand_pad - 20
                # Semi-transparent background pill
                draw.rounded_rectangle(
                    [(brand_x - brand_pad, brand_y - brand_pad),
                     (brand_x + brand_tw + brand_pad, brand_y + brand_th + brand_pad)],
                    radius=16, fill=(0, 0, 0, 160)
                )
                draw.text((brand_x, brand_y), brand_text, fill=(255, 255, 255, 220), font=font_brand)

            # Save
            bg_img.save(output_path, quality=95)
            print(f"   ✓ Thumbnail saved: {output_path}")
            return output_path

        except Exception as e:
            print(f"   ❌ Thumbnail generation failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def generate_video_ffmpeg(self, audio_segments: List[dict], output_path: str, tail_text: Optional[str] = None, tail_duration: float = 0.0, branding: str = "", music_file: Optional[str] = None, music_volume: float = 0.1):
        """
        Generate video using direct FFmpeg commands (Beta Engine).
        Significantly faster compositing but requires FFmpeg installed.
        """
        print(f"\n🎬 Generating video (FFMPEG Beta Engine)...")
        import subprocess
        
        temp_files = [] # Track for cleanup
        
        # Resolve FFmpeg executable
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            ffmpeg_exe = 'ffmpeg' # Fallback to system path
            
        # Resolve thumbnail path for title overlay
        thumbnail_path = None
        post_dir = None
        for segment in audio_segments:
            ap = segment.get('audio_path', '')
            if ap:
                p = os.path.dirname(os.path.dirname(ap))
                if os.path.isdir(p):
                    post_dir = p
                    break
        
        # Determine part number
        part_number = 1
        import re
        match = re.search(r'part(\d+)', os.path.basename(output_path), re.IGNORECASE)
        if match:
            part_number = int(match.group(1))
            
        if post_dir:
            candidates = [
                os.path.join(post_dir, f"thumbnail_part{part_number}.png"),
                os.path.join(post_dir, f"video_part{part_number}_thumbnail.png"),
                os.path.join(post_dir, "thumbnail.png")
            ]
            for c in candidates:
                if os.path.exists(c):
                    thumbnail_path = c
                    break
                    
        # Fallback dynamic generation if not found
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            print(f"   ⚠️  Thumbnail not pre-generated. Creating dynamic fallback...")
            import tempfile
            fallback_path = os.path.join(post_dir or tempfile.gettempdir(), f"temp_thumb_part{part_number}.png")
            p_title = "Reddit Story"
            p_sub = "AskReddit"
            p_score = 0
            if post_dir and os.path.exists(os.path.join(post_dir, "summary.json")):
                try:
                    with open(os.path.join(post_dir, "summary.json"), 'r', encoding='utf-8') as sf:
                        s_data = json.load(sf)
                        p_title = s_data.get('title', p_title)
                        p_sub = s_data.get('subreddit', p_sub)
                        p_score = s_data.get('score', p_score)
                except:
                    pass
            thumbnail_path = self.generate_thumbnail(
                title=p_title,
                subreddit=p_sub,
                part_number=part_number,
                total_parts=1,  # fallback
                output_path=fallback_path,
                score=p_score,
                branding=branding
            )
            
        # Distribute thumbnail_path to all title segments and detect title/credits
        for idx, segment in enumerate(audio_segments):
            # If the filename itself contains 'credits', tag it as credits
            if 'credits' in os.path.basename(segment.get('audio_path', '')).lower():
                segment['is_credits'] = True
            # If it's the very first segment and not yet tagged, or if it's the title segment
            if idx == 0 and not any(s.get('is_title') for s in audio_segments):
                segment['is_title'] = True
                
            if segment.get('is_title'):
                segment['thumbnail_path'] = thumbnail_path
        
        try:
            # 1. Prepare Audio (Using MoviePy for safety/compatibility)
            # We reuse the concatenation logic to get one solid audio file
            audio_clips = [AudioFileClip(s['audio_path']) for s in audio_segments]
            final_audio = concatenate_audioclips(audio_clips)
            total_duration = final_audio.duration + (tail_duration if (tail_text and tail_duration and tail_duration > 0) else 0)

            # Mix background music if enabled
            if music_file and music_file != "none":
                try:
                    music_path = os.path.join(self.music_dir, music_file)
                    if music_file.lower() == "random":
                        music_files = [f for f in os.listdir(self.music_dir) if f.lower().endswith(('.mp3', '.wav', '.m4a', '.aac'))]
                        if music_files:
                            music_path = os.path.join(self.music_dir, random.choice(music_files))
                        else:
                            music_path = None
                            print("⚠️ No music files found in 'music/' directory.")
                    
                    if music_path and os.path.exists(music_path):
                        print(f"   🎵 Adding background music (FFmpeg engine): {os.path.basename(music_path)} at volume {music_volume}")
                        music_clip = AudioFileClip(music_path)
                        
                        if music_clip.duration < total_duration:
                            music_clip = music_clip.loop(duration=total_duration)
                        else:
                            music_clip = music_clip.subclip(0, total_duration)
                        
                        music_clip = music_clip.volumex(music_volume)
                        final_audio = CompositeAudioClip([final_audio, music_clip])
                except Exception as e:
                    print(f"⚠️ Error mixing background music: {e}")
            
            output_dir = os.path.dirname(output_path)
            temp_audio_path = os.path.join(output_dir, "ffmpeg_audio_temp.m4a")
            final_audio.write_audiofile(temp_audio_path, codec='aac', logger=None)
            final_audio.close()
            temp_files.append(temp_audio_path)
            
            # 2. Get Background Video (Pure FFmpeg)
            print("   Preparing background (Direct FFmpeg)...")
            video_files = [f for f in os.listdir(self.backgrounds_dir) 
                          if f.lower().endswith(('.mp4', '.mov', '.avi'))]
            
            use_blank_bg = False
            if not video_files:
                print("⚠️  No background videos found — using blank background")
                use_blank_bg = True
                bg_path = None
            else:
                bg_file = random.choice(video_files)
                bg_path = os.path.join(self.backgrounds_dir, bg_file)
            
            temp_bg_path = os.path.join(output_dir, "ffmpeg_bg_temp.mp4")
            w = self.width
            h = self.height

            if use_blank_bg:
                # Generate a solid-color background using FFmpeg lavfi
                bg_color_hex = "141420"
                bg_cmd = [
                    ffmpeg_exe, '-y',
                    '-f', 'lavfi', '-i', f'color=c=0x{bg_color_hex}:s={w}x{h}:d={total_duration}:r=30',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    temp_bg_path
                ]
                print(f"   Generating blank background: {w}x{h}, {total_duration:.1f}s")
                subprocess.run(bg_cmd, check=True)
            else:
                # Check background duration for random seeking
                start_time = 0.0
                enable_loop = True

                try:
                    with VideoFileClip(bg_path) as clip:
                        bg_duration = clip.duration

                    if bg_duration > total_duration:
                        max_start = bg_duration - total_duration
                        start_time = random.uniform(0, max_start)
                        enable_loop = False
                        print(f"   Background is long enough ({bg_duration:.1f}s). Skipping to {start_time:.1f}s.")
                    else:
                        print(f"   Background is short ({bg_duration:.1f}s). Looping.")

                except Exception as e:
                    print(f"⚠️  Could not probe background duration: {e}. Defaulting to loop from start.")

                scale_filter = f"scale='iw*max({w}/iw\\,{h}/ih)':'ih*max({w}/iw\\,{h}/ih)',crop={w}:{h}"

                bg_cmd = [ffmpeg_exe, '-y']
                if start_time > 0:
                    bg_cmd.extend(['-ss', str(start_time)])
                if enable_loop:
                    bg_cmd.extend(['-stream_loop', '-1'])
                bg_cmd.extend(['-i', bg_path, '-vf', scale_filter, '-t', str(total_duration), '-an'])

                if self.hw_accel == 'nvenc':
                    bg_cmd.extend(['-c:v', 'h264_nvenc', '-rc', 'constqp', '-qp', '26', '-b:v', '0', '-preset', 'p2'])
                elif self.hw_accel == 'amf':
                    bg_cmd.extend(['-c:v', 'h264_amf', '-rc', 'cqp', '-qp_i', '26', '-qp_p', '26'])
                else:
                    bg_cmd.extend(['-c:v', 'libx264', '-preset', 'ultrafast'])

                bg_cmd.append(temp_bg_path)
                print(f"   Background Command: {' '.join(bg_cmd)}")
                subprocess.run(bg_cmd, check=True)

            temp_files.append(temp_bg_path)
            
            # 3. Generate Overlays
            print(f"   Generating {len(audio_segments)} overlay frames...")
            concat_lines = []
            
            current_author = None
            
            for i, segment in enumerate(audio_segments):
                if i % 10 == 0: print(f"     Processing segment {i+1}/{len(audio_segments)}...")
                
                # Check Duration
                duration = audio_clips[i].duration
                
                if segment.get('is_title') or segment.get('is_credits'):
                    # Create single full frame overlay for title/credits
                    overlay_path = self.create_full_frame_overlay(segment, current_author, branding=branding)
                    temp_files.append(overlay_path)
                    escape_path = overlay_path.replace('\\', '/')
                    concat_lines.append(f"file '{escape_path}'")
                    concat_lines.append(f"duration {duration}")
                else:
                    # Regular segment: Split into word groups of 1-2 words
                    words = segment['text'].split()
                    word_groups = []
                    for w_idx in range(0, len(words), 2):
                        word_groups.append(" ".join(words[w_idx:w_idx+2]))
                        
                    if not word_groups:
                        # Empty segment: Create a blank transparent overlay
                        blank_canvas = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
                        import tempfile
                        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                        blank_canvas.save(temp_file.name)
                        temp_file.close()
                        temp_files.append(temp_file.name)
                        
                        escape_path = temp_file.name.replace('\\', '/')
                        concat_lines.append(f"file '{escape_path}'")
                        concat_lines.append(f"duration {duration}")
                    else:
                        # Distribute duration proportionally
                        total_chars = sum(len(g) for g in word_groups)
                        if total_chars > 0:
                            durations = [duration * (len(g) / total_chars) for g in word_groups]
                        else:
                            durations = [duration / len(word_groups)] * len(word_groups)
                            
                        for group, dur in zip(word_groups, durations):
                            # Create a single full frame overlay for this group
                            group_segment = {
                                'text': group,
                                'font_name': 'impact.ttf',
                                'fontsize': 120 if self.mode == 'reel' or self.mode == 'short_reel' else 80,
                                'use_bg_box': False
                            }
                            overlay_path = self.create_full_frame_overlay(group_segment, current_author, branding=branding)
                            temp_files.append(overlay_path)
                            escape_path = overlay_path.replace('\\', '/')
                            concat_lines.append(f"file '{escape_path}'")
                            concat_lines.append(f"duration {dur}")

            if tail_text and tail_duration and tail_duration > 0:
                tail_segment = {'text': tail_text, 'author': '', 'use_bg_box': True, 'fontsize': 70 if self.mode == 'reel' or self.mode == 'short_reel' else 50}
                tail_overlay_path = self.create_full_frame_overlay(tail_segment, current_author, branding=branding)
                temp_files.append(tail_overlay_path)
                escape_tail = tail_overlay_path.replace('\\', '/')
                concat_lines.append(f"file '{escape_tail}'")
                concat_lines.append(f"duration {tail_duration}")
                
            # Create concat file
            concat_path = os.path.join(output_dir, "overlay_list.txt")
            with open(concat_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(concat_lines))
            temp_files.append(concat_path)
            
            # 4. Construct FFmpeg Command
            print("   Running FFmpeg render...")
            
            # Inputs:
            # -i temp_bg_path (Background)
            # -f concat -i concat_path (Overlay Stream)
            # -i temp_audio_path (Audio)
            
            # Select codec based on hw_accel
            if self.hw_accel == 'nvenc':
                v_codec = 'h264_nvenc'
            elif self.hw_accel == 'amf':
                v_codec = 'h264_amf'
            else:
                v_codec = 'libx264'

            cmd = [
                ffmpeg_exe, '-y',
                '-i', temp_bg_path,
                '-f', 'concat', '-safe', '0', '-pix_fmt', 'rgba', '-i', concat_path,
                '-i', temp_audio_path,
                '-filter_complex', '[0:v][1:v]overlay=0:0[outv]',
                '-map', '[outv]', '-map', '2:a',
                '-c:v', v_codec,
                '-c:a', 'aac',
                '-pix_fmt', 'yuv420p',
                '-r', '30'
            ]

            if not (tail_text and tail_duration and tail_duration > 0):
                cmd.append('-shortest')
            
            if self.hw_accel == 'nvenc':
                cmd.extend(['-preset', 'p4', '-rc', 'vbr', '-cq', '19', '-b:v', '8M'])
            elif self.hw_accel == 'amf':
                cmd.extend(['-quality', 'speed', '-rc', 'vbr_latency', '-qp_i', '19', '-qp_p', '19', '-b:v', '8M'])
            else:
                cmd.extend(['-preset', 'medium', '-crf', '18'])
                
            cmd.append(output_path)
            
            print(f"   Command: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
            
            print("✓ FFmpeg generation complete!")
            
            # Cleanup
            if self.use_gpu: # Maybe keep for debug if not gpu? No, clean always unless debug mode.
                pass 
                
            print("   Cleaning up temporary files...")
            for f in temp_files:
                try:
                    if os.path.exists(f): os.remove(f)
                except: pass
                
            return output_path
            
        except Exception as e:
            print(f"❌ FFmpeg Engine Error: {e}")
            import traceback
            traceback.print_exc()
            return None
