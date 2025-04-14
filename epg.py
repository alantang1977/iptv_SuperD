import os
import xml.etree.ElementTree as ET
import datetime
import gzip
from pprint import pprint
from io import StringIO, BytesIO

from iptv import IPTV, logging, conv_dict, clean_inline_comment

# 从环境变量中获取配置信息，如果未设置则使用默认值
EPG_GZ_DISABLED = os.environ.get('EPG_GZ_DISABLED', False)
EPG_SOURCE = os.environ.get('EPG_SOURCE', 'https://epg.v1.mk/fy.xml')
EPG_CHANNEL_MAP = os.environ.get('EPG_CHANNEL_MAP', 'epg.txt')

# 定义用于查找 EPG 信息名称和 URL 的键列表
_info_name_keys = ['generator-info-name', 'info-name', 'source-info-name']
_info_url_keys = ['generator-info-url', 'info-url', 'source-info-url']

class EPG:
    def __init__(self, *args, **kwargs):
        # 初始化 IPTV 实例并加载频道信息
        self.iptv = IPTV()
        self.iptv.load_channels()
        # 初始化 EPG 文档为 None
        self.epg_doc = None

    def fetch_epg(self):
        """
        从指定的 URL 获取 EPG 数据，并进行解压和解析
        """
        url = EPG_SOURCE
        try:
            # 使用 IPTV 实例的 fetch 方法获取 EPG 数据
            res = self.iptv.fetch(url)
            if res is None:
                logging.error(f'EPG 获取失败: {url}')
                return
            logging.info(f'EPG 获取成功: {url}')
            try:
                # 尝试对获取的数据进行解压
                content = gzip.decompress(res.content)
                logging.info('EPG 解压成功')
            except (gzip.BadGzipFile, AttributeError):
                # 如果解压失败，使用原始数据
                content = res.content
            # 解析 XML 数据
            self.epg_doc = ET.parse(BytesIO(content))
        except Exception as e:
            logging.error(f'解析 EPG 出错: {url} {e}')

    def load_channel_name_map(self):
        """
        从指定文件中加载频道名称映射信息
        """
        channel_map = {}
        try:
            with open(EPG_CHANNEL_MAP) as fp:
                for line in fp.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    line = clean_inline_comment(line)
                    channel_map.update(conv_dict(line))
        except FileNotFoundError:
            logging.error(f'频道名称映射文件 {EPG_CHANNEL_MAP} 未找到')
        return channel_map

    def convert_channel_name(self):
        """
        根据加载的频道名称映射信息，转换 EPG 文档中的频道名称
        """
        if self.epg_doc is None:
            logging.warning('EPG 文档未正确加载，无法进行频道名称转换')
            return
        channel_map = self.load_channel_name_map()
        root = self.epg_doc.getroot()
        for channel in root.findall('channel'):
            display_name_ele = channel.find('display-name')
            if display_name_ele is not None and display_name_ele.text in channel_map:
                old_name = display_name_ele.text
                new_name = channel_map[old_name]
                logging.debug(f'映射频道名: {old_name} => {new_name}')
                display_name_ele.text = new_name

    def cleanup(self):
        """
        清理 EPG 文档中不存在于 IPTV 频道列表中的频道和节目信息
        """
        if self.epg_doc is None:
            logging.warning('EPG 文档未正确加载，无法进行清理操作')
            return
        del_channel_ids = []
        reserved_channel_names = []
        root = self.epg_doc.getroot()
        for channel in root.findall('channel'):
            display_name_ele = channel.find('display-name')
            if display_name_ele is not None:
                name = display_name_ele.text
                if name not in self.iptv.channels.keys():
                    del_channel_ids.append(channel.get('id'))
                    root.remove(channel)
                else:
                    reserved_channel_names.append(name)

        for programme in root.findall('programme'):
            if programme.get('channel') in del_channel_ids:
                root.remove(programme)
            else:
                desc = programme.find('desc')
                if desc is not None:
                    programme.remove(desc)

        non_existed_channels = ', '.join([n for n in self.iptv.channels.keys() if n not in reserved_channel_names])
        logging.info(f'没有节目表的频道: {non_existed_channels}')

    def normalize_extras(self):
        """
        规范化 EPG 文档的额外信息，如日期、生成器信息等
        """
        if self.epg_doc is None:
            logging.warning('EPG 文档未正确加载，无法进行额外信息规范化操作')
            return
        def _existing_value(ele, try_keys):
            """
            尝试从元素的属性中获取指定键的值
            """
            if not isinstance(try_keys, list):
                try_keys = [try_keys]
            for k in try_keys:
                value = ele.get(k)
                if value:
                    return value
            return None

        def _normalize(n, u):
            """
            处理 51zmt 中 name 和 url 信息写反的情况
            """
            if 'epg.v1.mk' in (n or '') or 'epg.v1.mk' in (u or ''):
                n, u = u, n
            return n, u

        root = self.epg_doc.getroot()
        info_name = _existing_value(root, _info_name_keys)
        info_url = _existing_value(root, _info_url_keys)

        info_name, info_url = _normalize(info_name, info_url)

        root.attrib.clear()
        now = datetime.datetime.now(datetime.timezone.utc)
        root.set('date', now.strftime('%Y%m%d%H%M%S +0000'))
        root.set('generator-info-name', 'alantang1977/iptv_SuperD')
        root.set('generator-info-url', 'https://github.com/alantang1977/iptv_SuperD')
        root.set('source-info-name', info_name)
        root.set('source-info-url', info_url or EPG_SOURCE)

    def normalize(self):
        """
        对 EPG 文档进行规范化处理，包括频道名称转换、清理和额外信息规范化
        """
        self.convert_channel_name()
        self.cleanup()
        self.normalize_extras()

    def dumpb(self):
        """
        将 EPG 文档转换为字节流形式
        """
        if self.epg_doc is None:
            logging.warning('EPG 文档未正确加载，无法进行字节流转换')
            return b''
        root = self.epg_doc.getroot()
        ET.indent(root)
        return ET.tostring(root, encoding='utf-8', xml_declaration=True)

    def dumps(self):
        """
        将 EPG 文档转换为字符串形式
        """
        return self.dumpb().decode()

    def export_xml(self):
        """
        将 EPG 文档导出为 XML 文件
        """
        if self.epg_doc is None:
            logging.warning('EPG 文档未正确加载，无法导出 XML 文件')
            return
        dst = self.iptv.get_dist('epg.xml')
        try:
            with open(dst, 'w', encoding='utf-8') as fp:
                fp.write(self.dumps())
            logging.info(f'导出 xml: {dst}')
        except Exception as e:
            logging.error(f'导出 XML 文件时出错: {dst} {e}')

    def export_xml_gz(self):
        """
        将 EPG 文档导出为压缩的 XML 文件（.xml.gz）
        """
        if self.epg_doc is None:
            logging.warning('EPG 文档未正确加载，无法导出 XML.gz 文件')
            return
        dst = self.iptv.get_dist('epg.xml.gz')
        try:
            with open(dst, 'wb') as fp:
                fp.write(gzip.compress(self.dumpb()))
            logging.info(f'导出 xml.gz: {dst}')
        except Exception as e:
            logging.error(f'导出 XML.gz 文件时出错: {dst} {e}')

    def run(self):
        """
        运行 EPG 处理流程，包括获取数据、规范化处理和导出文件
        """
        self.fetch_epg()
        self.normalize()
        self.export_xml()

        if not EPG_GZ_DISABLED:
            self.export_xml_gz()


if __name__ == '__main__':
    epg = EPG()
    epg.run()
    
