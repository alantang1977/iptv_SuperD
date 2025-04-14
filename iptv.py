import os
from configparser import ConfigParser, NoOptionError
from collections import OrderedDict
import re
from urllib.parse import urlparse
import logging
import itertools
import typing as t
import json
from datetime import datetime
from itertools import islice

import requests
import zhconv

DEBUG = os.environ.get('DEBUG') is not None
IPTV_CONFIG = os.environ.get('IPTV_CONFIG') or 'config.ini'
IPTV_CHANNEL = os.environ.get('IPTV_CHANNEL') or 'channel.txt'
IPTV_DIST = os.environ.get('IPTV_DIST') or 'dist'
EXPORT_RAW = ConfigParser.BOOLEAN_STATES[os.environ.get('EXPORT_RAW', default=str(DEBUG)).lower()]
EXPORT_JSON = ConfigParser.BOOLEAN_STATES[os.environ.get('EXPORT_JSON', default=str(DEBUG)).lower()]

DEF_LINE_LIMIT = 10
DEF_REQUEST_TIMEOUT = 100
DEF_USER_AGENT = 'okhttp/4.12.0-iptv'
DEF_INFO_LINE = 'https://gcalic.v.myalicdn.com/gc/wgw05_1/index.m3u8?contentid=2820180516001'
DEF_EPG = 'https://raw.githubusercontent.com/JinnLynn/iptv/dist/epg.xml'
DEF_IPV4_FILENAME_SUFFIX = '-ipv4'
DEF_WHITELIST_PRIORITY = 10

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='[%(asctime)s][%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()])

# REF: https://github.com/bustawin/ordered-set-37
T = t.TypeVar("T")
class OrderedSet(t.MutableSet[T]):
    def __init__(self, iterable: t.Optional[t.Iterable[T]] = None):
        self._d = OrderedDict()
        if iterable is not None:
            for item in iterable:
                self._d[item] = None

    def add(self, x: T) -> None:
        self._d[x] = None

    def clear(self) -> None:
        self._d.clear()

    def discard(self, x: T) -> None:
        self._d.pop(x, None)

    def __getitem__(self, index) -> T:
        return list(self._d.keys())[index]

    def __contains__(self, x: object) -> bool:
        return x in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self) -> t.Iterator[T]:
        return iter(self._d.keys())

    def __str__(self):
        return str(list(self._d.keys()))

    def __repr__(self):
        return f"OrderedSet({list(self._d.keys())})"

class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, OrderedSet):
            return list(o)
        return super().default(o)

def json_dump(obj, fp=None, **kwargs):
    if fp:
        json.dump(obj, fp, cls=JSONEncoder, **kwargs)
    else:
        return json.dumps(obj, cls=JSONEncoder, **kwargs)

def conv_bool(v):
    if isinstance(v, str):
        v = v.lower()
        return v in ('true', '1', 'yes')
    return bool(v)

def conv_list(v):
    if isinstance(v, str):
        return [i.strip() for i in v.split(',') if i.strip()]
    return v

def conv_dict(v):
    if isinstance(v, str):
        result = {}
        items = v.split(',')
        for item in items:
            if '=' in item:
                key, value = item.split('=', 1)
                result[key.strip()] = value.strip()
        return result
    return v

def clean_inline_comment(v):
    return re.sub(r'#.*$', '', v).strip()

def is_ipv6(url):
    parsed = urlparse(url)
    netloc = parsed.netloc
    if ':' in netloc and '.' not in netloc:
        return True
    return False

class IPTV:
    def __init__(self, *args, **kwargs):
        self.config = ConfigParser()
        self.config.read(IPTV_CONFIG)
        self.channels = OrderedDict()
        self.cate_logos = {}  # 改为普通类属性
        self.channel_map = {}
        self.blacklist = OrderedSet()
        self.whitelist = OrderedSet()
        self.load_channels()

    def get_config(self, key, *convs, default=None):
        try:
            value = self.config.get('iptv', key)
            for conv in convs:
                value = conv(value)
            return value
        except NoOptionError:
            return default

    def _get_path(self, dist, filename):
        return os.path.join(dist, filename)

    def get_dist(self, filename, ipv4_suffix=False):
        if ipv4_suffix:
            base, ext = os.path.splitext(filename)
            filename = f"{base}{DEF_IPV4_FILENAME_SUFFIX}{ext}"
        return self._get_path(IPTV_DIST, filename)

    def get_cate_logos(self):
        logos = self.get_config('cate_logos', conv_dict)
        return logos if logos else {}

    @property
    def channel_map(self):
        channel_map = self.get_config('channel_map', conv_dict)
        return channel_map if channel_map else {}

    @property
    def blacklist(self):
        blacklist = self.get_config('blacklist', conv_list)
        return OrderedSet(blacklist) if blacklist else OrderedSet()

    @property
    def whitelist(self):
        whitelist = self.get_config('whitelist', conv_list)
        return OrderedSet(whitelist) if whitelist else OrderedSet()

    def load_channels(self):
        try:
            with open(IPTV_CHANNEL, 'r', encoding='utf-8') as f:
                for line in f:
                    line = clean_inline_comment(line)
                    if not line:
                        continue
                    parts = line.split(',', 1)
                    if len(parts) == 2:
                        name, url = parts
                        name = name.strip()
                        url = url.strip()
                        if name and url:
                            if name not in self.channels:
                                self.channels[name] = OrderedSet()
                            self.channels[name].add(url)
        except FileNotFoundError:
            logging.error(f"Channel file {IPTV_CHANNEL} not found.")

    def fetch(self, url):
        try:
            headers = {
                'User-Agent': DEF_USER_AGENT
            }
            response = requests.get(url, headers=headers, timeout=DEF_REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logging.error(f"Failed to fetch {url}: {e}")
            return None

    def fetch_sources(self):
        source_urls = self.get_config('source_urls', conv_list)
        if source_urls:
            for url in source_urls:
                content = self.fetch(url)
                if content:
                    lines = content.splitlines()
                    for i in range(0, len(lines), 2):
                        if i + 1 < len(lines):
                            info_line = lines[i]
                            url_line = lines[i + 1]
                            # 简单解析，假设info_line包含频道名
                            match = re.search(r'tvg-name="([^"]+)"', info_line)
                            if match:
                                name = match.group(1)
                                url = url_line.strip()
                                if name and url:
                                    if name not in self.channels:
                                        self.channels[name] = OrderedSet()
                                    self.channels[name].add(url)

    def is_port_necessary(self, scheme, netloc):
        default_ports = {
            'http': 80,
            'https': 443
        }
        if ':' in netloc:
            host, port_str = netloc.rsplit(':', 1)
            try:
                port = int(port_str)
                if scheme in default_ports and port == default_ports[scheme]:
                    return False
            except ValueError:
                pass
        return True

    def clean_channel_name(self, name):
        name = zhconv.convert(name, 'zh-cn')
        name = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9 ]', '', name)
        return name.strip()

    def add_channel_for_debug(self, name, url, org_name, org_url):
        if DEBUG:
            if name not in self.channels:
                self.channels[name] = OrderedSet()
            self.channels[name].add(url)

    def try_map_channel_name(self, name):
        return self.channel_map.get(name, name)

    def add_channel_uri(self, name, uri):
        if name not in self.channels:
            self.channels[name] = OrderedSet()
        self.channels[name].add(uri)

    def sort_channels(self):
        self.channels = OrderedDict(sorted(self.channels.items()))

    def stat_fetched_channels(self):
        total_channels = len(self.channels)
        total_urls = sum(len(urls) for urls in self.channels.values())
        logging.info(f"Fetched {total_channels} channels with {total_urls} URLs.")

    def is_on_blacklist(self, url):
        for pattern in self.blacklist:
            if re.search(pattern, url):
                return True
        return False

    def is_on_whitelist(self, url):
        for pattern in self.whitelist:
            if re.search(pattern, url):
                return True
        return False

    def enum_channel_uri(self, name, limit=None, only_ipv4=False):
        uris = self.channels.get(name, [])
        if only_ipv4:
            uris = [uri for uri in uris if not is_ipv6(uri)]
        if limit:
            uris = islice(uris, limit)
        return uris

    def export_info(self, fmt='m3u', fp=None):
        if fmt == 'm3u':
            self.export_m3u(fp=fp)
        elif fmt == 'txt':
            self.export_txt(fp=fp)
        elif fmt == 'json':
            self.export_json(fp=fp)

    def get_export_filename(self, filename, only_ipv4=False):
        if only_ipv4:
            base, ext = os.path.splitext(filename)
            filename = f"{base}{DEF_IPV4_FILENAME_SUFFIX}{ext}"
        return filename

    def export_m3u(self, only_ipv4=False, fp=None):
        if not fp:
            filename = self.get_export_filename('channels.m3u', only_ipv4)
            dist_path = self.get_dist(filename)
            fp = open(dist_path, 'w', encoding='utf-8')
        fp.write(f"#EXTM3U url-tvg=\"{DEF_EPG}\"\n")
        for name, uris in self.channels.items():
            for uri in self.enum_channel_uri(name, limit=DEF_LINE_LIMIT, only_ipv4=only_ipv4):
                if self.is_on_blacklist(uri):
                    continue
                info_line = f"#EXTINF:-1 tvg-name=\"{name}\",{name}\n"
                fp.write(info_line)
                fp.write(f"{uri}\n")
        if not isinstance(fp, str):
            fp.close()

    def export_txt(self, only_ipv4=False, fp=None):
        if not fp:
            filename = self.get_export_filename('channels.txt', only_ipv4)
            dist_path = self.get_dist(filename)
            fp = open(dist_path, 'w', encoding='utf-8')
        for name, uris in self.channels.items():
            for uri in self.enum_channel_uri(name, limit=DEF_LINE_LIMIT, only_ipv4=only_ipv4):
                if self.is_on_blacklist(uri):
                    continue
                fp.write(f"{name},{uri}\n")
        if not isinstance(fp, str):
            fp.close()

    def export_json(self, only_ipv4=False, fp=None):
        data = {}
        for name, uris in self.channels.items():
            valid_uris = [uri for uri in self.enum_channel_uri(name, limit=DEF_LINE_LIMIT, only_ipv4=only_ipv4) if not self.is_on_blacklist(uri)]
            if valid_uris:
                data[name] = valid_uris
        if not fp:
            filename = self.get_export_filename('channels.json', only_ipv4)
            dist_path = self.get_dist(filename)
            with open(dist_path, 'w', encoding='utf-8') as f:
                json_dump(data, f, ensure_ascii=False, indent=4)
        else:
            json_dump(data, fp, ensure_ascii=False, indent=4)

    def export_raw(self):
        if EXPORT_RAW:
            self.export_info(fmt='m3u')
            self.export_info(fmt='txt')
            self.export_info(fmt='json')

    def export(self):
        self.export_info(fmt='m3u')
        self.export_info(fmt='m3u', only_ipv4=True)
        self.export_info(fmt='txt')
        self.export_info(fmt='txt', only_ipv4=True)
        if EXPORT_JSON:
            self.export_info(fmt='json')
            self.export_info(fmt='json', only_ipv4=True)

    def run(self):
        self.fetch_sources()
        self.sort_channels()
        self.stat_fetched_channels()
        self.export_raw()
        self.export()


if __name__ == '__main__':
    iptv = IPTV()
    iptv.run()
