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
import time
from concurrent.futures import ThreadPoolExecutor

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
                self.add(item)

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
    if fp is None:
        return json.dumps(obj, cls=JSONEncoder, **kwargs)
    return json.dump(obj, fp, cls=JSONEncoder, **kwargs)

def conv_bool(v):
    return ConfigParser.BOOLEAN_STATES.get(v.lower(), False)

def conv_list(v):
    return [i.strip() for i in v.split(',') if i.strip()]

def conv_dict(v):
    result = {}
    for line in v.splitlines():
        line = line.strip()
        if line:
            key, value = line.split(None, 1)
            result[key] = value
    return result

def clean_inline_comment(v):
    return v.split('#', 1)[0].strip()

def is_ipv6(url):
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if ':' in netloc and '[' not in netloc:
            return True
    except ValueError:
        pass
    return False

class IPTV:
    def __init__(self, *args, **kwargs):
        self._cate_logos = None
        self._channel_map = None
        self._blacklist = None
        self._whitelist = None

        self.raw_config = None
        self.raw_channels = {}
        self.channel_cates = OrderedDict()
        self.channels = {}

    def get_config(self, key, *convs, default=None):
        if not self.raw_config:
            self.raw_config = ConfigParser()
            self.raw_config.read([c.strip() for c in IPTV_CONFIG.split(',')])

        try:
            value = self.raw_config.get('config', key)
            value = clean_inline_comment(value)
            if convs:
                for conv in convs:
                    value = conv(value)
        except NoOptionError:
            return default
        except Exception as e:
            logging.error(f'获取配置出错: {key} {e}')
            return default
        return value

    def _get_path(self, dist, filename):
        if not os.path.isdir(dist):
            os.makedirs(dist, exist_ok=True)
        abspath = os.path.join(dist, filename)
        if not os.path.isdir(os.path.dirname(abspath)):
            os.makedirs(os.path.dirname(abspath), exist_ok=True)
        return abspath

    def get_dist(self, filename, ipv4_suffix=False):
        parts = filename.rsplit('.', 1)
        if ipv4_suffix:
            parts[0] = f'{parts[0]}{DEF_IPV4_FILENAME_SUFFIX}'
        return self._get_path(IPTV_DIST, '.'.join(parts))

    @property
    def cate_logos(self):
        if self._cate_logos is None:
            self._cate_logos = self.get_config('logo_cate', conv_dict, default={})
        return self._cate_logos

    @property
    def channel_map(self):
        if self._channel_map is None:
            self._channel_map = self.get_config('channel_map', conv_dict, default={})
        return self._channel_map

    @property
    def blacklist(self):
        if self._blacklist is None:
            self._blacklist = self.get_config('blacklist', conv_list, default=[])
        return self._blacklist

    @property
    def whitelist(self):
        if self._whitelist is None:
            self._whitelist = self.get_config('whitelist', conv_list, default=[])
        return self._whitelist

    def load_channels(self):
        for f in IPTV_CHANNEL.split(','):
            current = ''
            with open(f) as fp:
                for line in fp.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('CATE:'):
                        current = line[5:].strip()
                        self.channel_cates.setdefault(current, OrderedSet())
                    else:
                        if not current:
                            logging.warning(f'忽略没有指定分类的频道: {line}')
                            continue

                        if line.startswith('-'):
                            line = line[1:].strip()
                            if line in self.channel_cates[current]:
                                self.channel_cates[current].remove(line)
                        else:
                            self.channel_cates[current].add(line)

        for v in self.channel_cates.values():
            for c in v:
                self.channels.setdefault(c, [])

    def fetch(self, url):
        headers = {'User-Agent': DEF_USER_AGENT}
        res = requests.get(url, timeout=DEF_REQUEST_TIMEOUT, headers=headers)
        res.raise_for_status()
        return res

    def fetch_sources(self):
        sources = self.get_config('source', conv_list, default=[])
        success_count = 0
        failed_sources = []
        for url in sources:
            try:
                res = self.fetch(url)
            except Exception as e:
                logging.warning(f'获取失败: {url} {e}')
                failed_sources.append(url)
                continue
            is_m3u = any('#EXTINF' in l.decode() for l in islice(res.iter_lines(), 10))
            logging.info(f'获取成功: {"M3U" if is_m3u else "TXT"} {url}')
            success_count = success_count + 1

            cur_cate = None

            for line in res.iter_lines():
                line = line.decode().strip()
                if not line:
                    continue

                if is_m3u:
                    if line.startswith("#EXTINF"):
                        match = re.search(r'group-title="(.*?)",(.*)', line)
                        if match:
                            cur_cate = match.group(1).strip()
                            chl_name = match.group(2).strip()
                    elif not line.startswith("#"):
                        channel_url = line.strip()
                        self.add_channel_uri(chl_name, channel_url)
                else:
                    if "#genre#" in line:
                        cur_cate = line.split(",")[0].strip()
                    elif cur_cate:
                        match = re.match(r"^(.*?),(.*?)$", line)
                        if match:
                            chl_name = match.group(1).strip()
                            channel_url = match.group(2).strip()
                            self.add_channel_uri(chl_name, channel_url)
        logging.info(f'源读取完毕: 成功: {success_count} 失败: {len(failed_sources)}')
        if failed_sources:
            logging.warning(f'获取失败的源: {failed_sources}')
        self.stat_fetched_channels()

    def is_port_necessary(self, scheme, netloc):
        if netloc[-1] == ']':
            return False

        out = netloc.rsplit(":", 1)
        if len(out) == 1:
            return False
        else:
            try:
                port = int(out[1])
                if scheme == 'http' and port == 80:
                    return True
                if scheme == 'https' and port == 443:
                    return True
            except ValueError:
                return False
        return False

    def clean_channel_name(self, name):
        def re_subs(s, *reps):
            for rep in reps:
                r = ''
                c = 0
                if len(rep) == 1:
                    p = rep[0]
                elif len(rep) == 2:
                    p, r = rep
                else:
                    p, r, c = rep
                s = re.sub(p, r, s, c)
            return s
        def any_startswith(s, *args):
            return any([re.search(fr'^{r}', s, re.IGNORECASE) for r in args])

        def any_in(s, *args):
            return any(a in s for a in args)

        # 繁 => 简
        jap = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7A3]')  # \uAC00-\uD7A3为匹配韩文的，其余为日文
        if not jap.search(name):
            name = zhconv.convert(name, 'zh-cn', {'「': '「', '」': '」'})

        if name.startswith('CCTV'):
            name = re_subs(name,
                                (r'-[(HD)0]*', ),                           # CCTV-0 CCTV-HD
                                (r'(CCTV[1-9][0-9]?[\+K]?).*', r'\1')
            )
            # FIX:
            # CCTV4美洲 ... => CCTV4
        elif name.startswith('CETV'):
            name = re_subs(name,
                                (r'[ -][(HD)0]*', ),
                                (r'(CETV[1-4]).*', r'\1'),
            )
        elif any_startswith(name, 'NewTV', 'CHC', 'iHOT'):
            for p in ['NewTV', 'CHC', 'iHOT']:
                name = re.sub(fr'^{p}', p, name, 1, re.IGNORECASE)
                if not name.startswith(p):
                    continue
                name = re_subs(name,
                                    (re.compile(f'{p} +'), p, 1),
                                    (r'(.*) +.*', r'\1')
                )
        elif re.match(r'^TVB[^s]', name, re.IGNORECASE):
            name = name.replace(' ', '')
        return name

    def add_channel_for_debug(self, name, url, org_name, org_url):
        if name not in self.raw_channels:
            self.raw_channels.setdefault(name, OrderedDict(source_names=set(), source_urls=set(), lines=[]))

        self.raw_channels[name]['source_names'].add(org_name)
        self.raw_channels[name]['source_urls'].add(org_url)

        for u in self.raw_channels[name]['lines']:
            if u['uri'] == url:
                u['count'] += u['count'] + 1
                return
        self.raw_channels[name]['lines'].append({'uri': url, 'count': 1, 'ipv6': is_ipv6(url)})

    def try_map_channel_name(self, name):
        if name in self.channel_map.keys():
            o_name = name
            name = self.channel_map[name]
            logging.debug(f'映射频道名: {o_name} => {name}')
        return name

    def test_response_time(self, url):
        try:
            start_time = time.time()
            headers = {'User-Agent': DEF_USER_AGENT}
            requests.get(url, timeout=DEF_REQUEST_TIMEOUT, headers=headers)
            end_time = time.time()
            return end_time - start_time
        except Exception as e:
            return None

    def add_channel_uri(self, name, uri):
        uri = re.sub(r'\$.*$', '', uri)

        name = self.try_map_channel_name(name)

        # 处理频道名
        org_name = name
        name = self.clean_channel_name(name)
        if org_name != name:
            logging.debug(f'规范频道名: {org_name} => {name}')

        name = self.try_map_channel_name(name)

        changed = False
        p = urlparse(uri)
        try:
            if self.is_port_necessary(p.scheme, p.netloc):
                changed = True
                p = p._replace(netloc=p.netloc.rsplit(':', 1)[0])
        except Exception as e:
            logging.debug(f'频道线路地址出错: {name} {uri} {e}')
            return

        url = p.geturl() if changed else uri

        self.add_channel_for_debug(name, url, org_name, uri)

        if name not in self.channels:
            return

        if self.is_on_blacklist(url):
            logging.debug(f'黑名单忽略: {name} {uri}')
            return

        response_time = self.test_response_time(url)
        if response_time is None:
            logging.debug(f'响应时间异常，忽略: {name} {uri}')
            return

        priority = DEF_WHITELIST_PRIORITY if self.is_on_whitelist(url) else 0
        for u in self.channels[name]:
            if u['uri'] == url:
                u['count'] = u['count'] + 1
                u['priority'] = u['count'] + priority
                u['response_time'] = response_time
                return
        self.channels[name].append({'uri': url, 'priority': priority + 1, 'count': 1, 'ipv6': is_ipv6(url), 'response_time': response_time})

    def sort_channels(self):
        for k in self.channels:
            self.channels[k].sort(key=lambda i: i.get('response_time', float('inf')))

    def stat_fetched_channels(self):
        line_num = sum([len(c) for c in self.channels])
        logging.info(f'获取的所需: 频道: {len(self.channels)} 线路: {line_num}')
        # TODO: 输出没有获取到任何线路的频道

    def is_on_blacklist(self, url):
        # TODO: 支持regex
        return any(b in url for b in self.blacklist)
        # return any(re.search(re.compile(b), url) for b in self.blacklist)

    def is_on_whitelist(self, url):
        # TODO: 支持regex
        return any(b in url for b in self.whitelist)

    def enum_channel_uri(self, name, limit=None, only_ipv4=False):
        if name not in self.channels:
            return []
        if limit is None:
            limit = self.get_config('limit', int, default=DEF_LINE_LIMIT)
        index = 0
        for chl in self.channels[name]:
            if only_ipv4 and chl['ipv6']:  # 修复后的代码
                continue
            index = index + 1
            if isinstance(limit, int) and limit > 0 and index > limit:
                return
            yield index, chl

    def export_info(self, fmt='m3u', fp=None):
        if self.get_config('disable_export_info', conv_bool, default=False):
            return
        day = datetime.now().strftime('%Y-%m-%d')
        url = DEF_INFO_LINE
        output = []

        if fmt == 'm3u':
            logo_url_prefix = self.get_config('logo_url_prefix', lambda s: s.rstrip('/'))
            output.append(f'#EXTINF:-1 tvg-id="1" tvg-name="{day}" tvg-logo="{logo_url_prefix}/{day}.png",{day}')
            output.append(url)

        if fp:
            fp.write('\n'.join(output))
        else:
            # 确保 dist 目录存在
            dist_path = self.get_dist('')
            if not os.path.exists(dist_path):
                os.makedirs(dist_path)

            # 生成文件名
            if fmt == 'm3u':
                filename = f'{day}.m3u'
            elif fmt == 'json':
                filename = f'{day}.json'
            else:
                filename = f'{day}.txt'

            file_path = os.path.join(dist_path, filename)
            with open(file_path, 'w') as f:
                f.write('\n'.join(output))

    
