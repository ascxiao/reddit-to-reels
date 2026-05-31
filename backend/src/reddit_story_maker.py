"""
Reddit Story Maker. Fetches and processes Reddit posts for video generation.

Author: Faheem Alvi
GitHub: https://github.com/FaheemAlvii
LinkedIn: https://www.linkedin.com/in/faheem-alvi
Email: faheemalvi2000@gmail.com
License: CC BY-NC 4.0
"""
import requests
import json
import os
import sys
# Ensure standard streams support Unicode (especially on Windows)
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("reddit_story_maker")


if getattr(sys, "frozen", False):
    PROJECT_ROOT = os.path.dirname(sys.executable)
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class RedditStoryMaker:
    """
    Fetches Reddit posts from a specified subreddit with customizable filters,
    tracks used posts, and saves post data organized by post ID.
    """
    
    def __init__(self, config_filename: str = "config.json"):
        """Initialize with configuration file."""
        self.config_path = os.path.join(PROJECT_ROOT, config_filename)
        self.config = self._load_config(self.config_path)
        self.used_posts = self._load_used_posts()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Config file not found: {config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")
    
    def _load_used_posts(self) -> List[str]:
        """Load list of already used post IDs."""
        used_posts_file = self.config['output']['used_posts_file']
        # Resolve absolute path for used_posts_file
        self.used_posts_path = os.path.join(PROJECT_ROOT, used_posts_file)
        
        if os.path.exists(self.used_posts_path):
            try:
                with open(self.used_posts_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse {self.used_posts_path}, starting fresh")
                return []
        return []
    
    def _save_used_posts(self):
        """Save the list of used post IDs."""
        with open(self.used_posts_path, 'w', encoding='utf-8') as f:
            json.dump(self.used_posts, f, indent=2)
        logger.info(f"✓ Updated used posts list: {len(self.used_posts)} posts tracked")
    
    def _clean_html(self, raw_html: str) -> str:
        """Helper to strip HTML tags and decode entities from RSS content."""
        if not raw_html:
            return ""
        import html
        import re
        unescaped = html.unescape(raw_html)
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', unescaped)
        return cleantext.strip()

    def _convert_url_to_rss(self, url: str) -> str:
        """Map a standard Reddit JSON endpoint URL to its equivalent RSS endpoint."""
        import re
        # Subreddit listing (e.g., https://www.reddit.com/r/AmItheAsshole/hot.json?limit=25)
        if "/r/" in url and ("/hot.json" in url or "/new.json" in url or "/top.json" in url or "/rising.json" in url):
            sort_match = re.search(r'/(hot|new|top|rising)\.json', url)
            sort = sort_match.group(1) if sort_match else "hot"
            sub_match = re.search(r'/r/([a-zA-Z0-9_]+)', url)
            sub = sub_match.group(1) if sub_match else "AskReddit"
            return f"https://www.reddit.com/r/{sub}/{sort}/.rss"
        
        # Comments details (e.g., https://www.reddit.com/comments/post_id/.json)
        if "/comments/" in url:
            clean_url = url.split('?')[0]
            if clean_url.endswith('.json'):
                clean_url = clean_url[:-5]
            return clean_url.rstrip('/') + '/.rss'
            
        return url

    def _rss_to_subreddit_json(self, rss_content: bytes) -> Dict:
        """Translate subreddit listing XML/RSS to the expected JSON Listing structure."""
        import xml.etree.ElementTree as ET
        import re
        
        root = ET.fromstring(rss_content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        children = []
        for entry in root.findall('atom:entry', ns):
            title_elem = entry.find('atom:title', ns)
            title = title_elem.text if title_elem is not None else ""
            
            link_elem = entry.find('atom:link', ns)
            url = link_elem.attrib['href'] if link_elem is not None else ""
            
            post_id = ""
            match = re.search(r'/comments/([a-z0-9]+)/', url)
            if match:
                post_id = match.group(1)
            else:
                id_elem = entry.find('atom:id', ns)
                id_text = id_elem.text if id_elem is not None else ""
                id_match = re.search(r't3_([a-z0-9]+)', id_text)
                if id_match:
                    post_id = id_match.group(1)
            
            author_elem = entry.find('atom:author/atom:name', ns)
            author = author_elem.text if author_elem is not None else "Anonymous"
            if author.startswith("/u/"):
                author = author[3:]
                
            content_elem = entry.find('atom:content', ns)
            content_html = content_elem.text if content_elem is not None else ""
            selftext = self._clean_html(content_html)
            
            category_elem = entry.find('atom:category', ns)
            subreddit = category_elem.attrib['term'] if category_elem is not None else "unknown"
            
            updated_elem = entry.find('atom:updated', ns)
            created_utc = time.time()
            if updated_elem is not None and updated_elem.text:
                try:
                    dt = datetime.fromisoformat(updated_elem.text.replace('Z', '+00:00'))
                    created_utc = dt.timestamp()
                except Exception:
                    pass
                    
            post_data = {
                'id': post_id,
                'title': title,
                'author': author,
                'subreddit': subreddit,
                'score': 1000,
                'upvote_ratio': 0.95,
                'num_comments': 100,
                'created_utc': created_utc,
                'url': url,
                'permalink': f"/r/{subreddit}/comments/{post_id}/" if post_id else "",
                'selftext': selftext,
                'over_18': False,
                'is_video': False
            }
            
            children.append({
                'kind': 't3',
                'data': post_data
            })
            
        return {
            'kind': 'Listing',
            'data': {
                'children': children
            }
        }

    def _rss_to_post_details_json(self, rss_content: bytes) -> List:
        """Translate post details & comments XML/RSS to the expected two-Listing JSON structure."""
        import xml.etree.ElementTree as ET
        import re
        
        root = ET.fromstring(rss_content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        entries = root.findall('atom:entry', ns)
        if not entries:
            return []
            
        orig_entry = entries[0]
        orig_title_elem = orig_entry.find('atom:title', ns)
        orig_title = orig_title_elem.text if orig_title_elem is not None else ""
        
        orig_link_elem = orig_entry.find('atom:link', ns)
        orig_url = orig_link_elem.attrib['href'] if orig_link_elem is not None else ""
        
        orig_id = ""
        match = re.search(r'/comments/([a-z0-9]+)/', orig_url)
        if match:
            orig_id = match.group(1)
            
        orig_author_elem = orig_entry.find('atom:author/atom:name', ns)
        orig_author = orig_author_elem.text if orig_author_elem is not None else "Anonymous"
        if orig_author.startswith("/u/"):
            orig_author = orig_author[3:]
            
        orig_content_elem = orig_entry.find('atom:content', ns)
        orig_content_html = orig_content_elem.text if orig_content_elem is not None else ""
        orig_selftext = self._clean_html(orig_content_html)
        
        category_elem = orig_entry.find('atom:category', ns)
        subreddit = category_elem.attrib['term'] if category_elem is not None else "unknown"
        
        orig_updated = orig_entry.find('atom:updated', ns)
        orig_created_utc = time.time()
        if orig_updated is not None and orig_updated.text:
            try:
                dt = datetime.fromisoformat(orig_updated.text.replace('Z', '+00:00'))
                orig_created_utc = dt.timestamp()
            except Exception:
                pass
                
        post_data = {
            'id': orig_id,
            'title': orig_title,
            'author': orig_author,
            'subreddit': subreddit,
            'score': 1000,
            'upvote_ratio': 0.95,
            'num_comments': len(entries) - 1,
            'created_utc': orig_created_utc,
            'url': orig_url,
            'permalink': f"/r/{subreddit}/comments/{orig_id}/" if orig_id else "",
            'selftext': orig_selftext,
            'over_18': False,
            'is_video': False
        }
        
        post_listing = {
            'kind': 'Listing',
            'data': {
                'children': [
                    {
                        'kind': 't3',
                        'data': post_data
                    }
                ]
            }
        }
        
        comment_children = []
        for comment_entry in entries[1:]:
            c_author_elem = comment_entry.find('atom:author/atom:name', ns)
            c_author = c_author_elem.text if c_author_elem is not None else "Anonymous"
            if c_author.startswith("/u/"):
                c_author = c_author[3:]
                
            c_content_elem = comment_entry.find('atom:content', ns)
            c_content_html = c_content_elem.text if c_content_elem is not None else ""
            c_text = self._clean_html(c_content_html)
            
            c_updated = comment_entry.find('atom:updated', ns)
            c_created_utc = time.time()
            if c_updated is not None and c_updated.text:
                try:
                    dt = datetime.fromisoformat(c_updated.text.replace('Z', '+00:00'))
                    c_created_utc = dt.timestamp()
                except Exception:
                    pass
                    
            comment_children.append({
                'kind': 't1',
                'data': {
                    'author': c_author,
                    'body': c_text,
                    'score': 100,
                    'created_utc': c_created_utc
                }
            })
            
        comments_listing = {
            'kind': 'Listing',
            'data': {
                'children': comment_children
            }
        }
        
        return [post_listing, comments_listing]

    def _fetch_via_rss(self, url: str) -> Optional[Any]:
        """Fetch Reddit RSS feed and translate it transparently to the expected JSON layout."""
        try:
            rss_url = self._convert_url_to_rss(url)
            logger.info(f"🔄 RSS Fallback: Fetching from {rss_url}")
            rss_headers = self.headers.copy()
            rss_headers['Accept'] = 'application/xml, text/xml, */*'
            rss_headers.pop('Accept-Language', None)
            response = requests.get(rss_url, headers=rss_headers, timeout=10)
            response.raise_for_status()
            
            if "/comments/" in url:
                return self._rss_to_post_details_json(response.content)
            else:
                return self._rss_to_subreddit_json(response.content)
        except Exception as ex:
            logger.error(f"✗ RSS fallback failed for {url}: {ex}")
            return None

    def _fetch_json(self, url: str) -> Optional[Any]:
        """Fetch JSON data from a URL, automatically falling back to RSS if blocked with a 403."""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            # Check for 403 Forbidden to trigger transparent RSS fallback
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 403:
                logger.warning(f"⚠️ Reddit returned 403 Forbidden for {url}. Activating automatic RSS fallback...")
                return self._fetch_via_rss(url)
            logger.error(f"✗ Error fetching {url}: {e}")
            return None
    
    def _meets_filters(self, post_data: Dict) -> Tuple[bool, str]:
        """
        Check if a post meets all configured filters.
        Returns (meets_filters, reason_if_not)
        """
        filters = self.config['filters']
        
        # Check upvotes
        if post_data.get('score', 0) < filters['min_upvotes']:
            return False, f"Upvotes ({post_data.get('score', 0)}) below minimum ({filters['min_upvotes']})"
        
        # Check comments
        num_comments = post_data.get('num_comments', 0)
        if num_comments < filters['min_comments']:
            return False, f"Comments ({num_comments}) below minimum ({filters['min_comments']})"
        if num_comments > filters['max_comments']:
            return False, f"Comments ({num_comments}) above maximum ({filters['max_comments']})"
        
        # Check age
        created_utc = post_data.get('created_utc', 0)
        post_age = datetime.now(timezone.utc) - datetime.fromtimestamp(created_utc, timezone.utc)
        min_age = timedelta(hours=filters['min_age_hours'])
        max_age = timedelta(hours=filters['max_age_hours'])
        
        if post_age < min_age:
            return False, f"Post too new ({post_age.total_seconds()/3600:.1f}h < {filters['min_age_hours']}h)"
        if post_age > max_age:
            return False, f"Post too old ({post_age.total_seconds()/3600:.1f}h > {filters['max_age_hours']}h)"
        
        # Check NSFW
        if post_data.get('over_18', False) and not filters['allow_nsfw']:
            return False, "Post is NSFW"
        
        # Check selftext requirement
        if filters['require_selftext'] and not post_data.get('selftext', '').strip():
            return False, "Post has no selftext"
        
        return True, "All filters passed"
    
    def fetch_subreddit_posts(self, subreddit: str = None, limit: int = 25, sort: str = "hot") -> List[Dict]:
        """
        Fetch posts from the configured subreddit.
        Returns list of post data dictionaries.
        sort: one of 'best', 'hot', 'new', 'rising', 'top'
        """
        if not subreddit:
            # Fallback for backward compatibility or default
            subreddit = self.config.get('subreddit', 'AskReddit')
        
        valid_sorts = ["best", "hot", "new", "rising", "top"]
        if sort not in valid_sorts:
            sort = "hot"
            
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}"
        if sort == "top":
            url += "&t=week"
        
        logger.info(f"Fetching posts from r/{subreddit}...")
        data = self._fetch_json(url)
        
        if not data:
            return []
        
        posts = []
        children = data.get('data', {}).get('children', [])
        
        for child in children:
            if child.get('kind') == 't3':  # t3 = post
                posts.append(child.get('data', {}))
        
        logger.info(f"✓ Retrieved {len(posts)} posts from subreddit")
        return posts
    
    def find_suitable_post(self) -> Optional[Dict]:
        """
        Find a post that meets all filters and hasn't been used yet.
        Returns the post data or None if no suitable post found.
        """
        # Get subreddits list (support both new list and old single string for backward compat)
        subreddits = self.config.get('subreddits', [])
        if not subreddits and 'subreddit' in self.config:
            subreddits = [self.config['subreddit']]
            
        if not subreddits:
            print("Error: No subreddits configured")
            return None

        request_delay = self.config.get('request_delay', 2.0)
        suitable_posts = []
        
        for i, subreddit in enumerate(subreddits):
            # Add delay between subreddit requests (but not before the first one)
            if i > 0:
                logger.info(f"Waiting {request_delay}s before checking next subreddit...")
                time.sleep(request_delay)
                
            posts = self.fetch_subreddit_posts(subreddit=subreddit, limit=100)
            
            logger.info(f"Filtering posts from r/{subreddit}...")
            logger.info(f"   Already used: {len(self.used_posts)} posts")
            
            for post in posts:
                post_id = post.get('id')
                
                # Skip if already used
                if post_id in self.used_posts:
                    continue
                
                # Check filters
                meets_filters, reason = self._meets_filters(post)
                
                if meets_filters:
                    suitable_posts.append(post)
                else:
                    logger.info(f"   Skipped {post_id}: {reason}")
        
        if suitable_posts:
            import random
            selected = random.choice(suitable_posts)
            logger.info(f"✓ Found {len(suitable_posts)} suitable post candidates across configured subreddits.")
            logger.info(f"✓ Selected random post:")
            logger.info(f"   ID: {selected.get('id')}")
            logger.info(f"   Subreddit: r/{selected.get('subreddit')}")
            logger.info(f"   Title: {selected.get('title', 'N/A')[:80]}...")
            logger.info(f"   Upvotes: {selected.get('score', 0)}")
            logger.info(f"   Comments: {selected.get('num_comments', 0)}")
            return selected

        logger.warning("No suitable posts found matching the criteria in any configured subreddit.")
        return None
    
    def fetch_post_details(self, post_url: str) -> Optional[List]:
        """
        Fetch detailed post data including comments.
        Returns [post_data, comments_data] or None.
        """
        # Ensure URL ends with .json
        if not post_url.endswith('.json'):
            post_url = post_url.rstrip('/') + '/.json'
        
        logger.info(f"Fetching post details...")
        data = self._fetch_json(post_url)
        
        if data and isinstance(data, list) and len(data) >= 2:
            logger.info(f"Retrieved post details with comments")
            return data
        
        logger.error(f"Failed to retrieve post details")
        return None
    
    def save_post_data(self, post_id: str, post_data: Dict, full_data: List):
        """
        Save post data to a folder named by post_id.
        Saves both the summary and full JSON data.
        """
        posts_dir = os.path.join(PROJECT_ROOT, self.config['output']['posts_directory'])
        post_folder = os.path.join(posts_dir, post_id)
        
        # Create directory
        os.makedirs(post_folder, exist_ok=True)
        
        # Save summary info
        summary = {
            'id': post_id,
            'title': post_data.get('title'),
            'author': post_data.get('author'),
            'subreddit': post_data.get('subreddit'),
            'score': post_data.get('score'),
            'upvote_ratio': post_data.get('upvote_ratio'),
            'num_comments': post_data.get('num_comments'),
            'created_utc': post_data.get('created_utc', 0),
            'created_datetime': datetime.fromtimestamp(post_data.get('created_utc', 0), timezone.utc).isoformat(),
            'url': post_data.get('url'),
            'permalink': post_data.get('permalink'),
            'selftext': post_data.get('selftext'),
            'over_18': post_data.get('over_18'),
            'is_video': post_data.get('is_video'),
            'downloaded_at': datetime.now(timezone.utc).isoformat()
        }
        
        summary_path = os.path.join(post_folder, 'summary.json')
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        # Save full data (post + comments)
        full_path = os.path.join(post_folder, 'full_data.json')
        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved post data to: {post_folder}")
        logger.info(f"   - summary.json (key information)")
        logger.info(f"   - full_data.json (complete post + comments)")
    
    def process_new_post(self) -> Optional[str]:
        """
        Main workflow: Find a suitable post, fetch details, and save.
        Returns post_id if successful, None otherwise.
        """
        # Find a suitable post
        post = self.find_suitable_post()
        
        if not post:
            return None
        
        post_id = post.get('id')
        post_url = post.get('url')
        
        # Fetch full post details
        full_data = self.fetch_post_details(post_url)
        
        if not full_data:
            return None
        
        # Save the data
        self.save_post_data(post_id, post, full_data)
        
        # Mark as used
        self.used_posts.append(post_id)
        self._save_used_posts()
        
        logger.info(f"Saved post: {post_id}")
        return post_id


def main():
    """Main entry point."""
    print("=" * 60)
    print("Reddit Story Maker")
    print("=" * 60)
    
    try:
        maker = RedditStoryMaker()
        success = maker.process_new_post()
        
        if success:
            print("\n" + "=" * 60)
            print("Process completed successfully!")
            print("=" * 60)
        else:
            print("\n" + "=" * 60)
            print("WARNING: No suitable post found matching the criteria.")
            print("=" * 60)
            
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
