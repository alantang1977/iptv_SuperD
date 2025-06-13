<div align="center">
  <img src="https://raw.githubusercontent.com/alantang1977/X/main/Pictures/SuperMAN.png" alt="logo"/>
  <h1 align="center">iptv_SuperD</h1>
</div>

<div align="center">一个可高度自定义的IPTV接口更新项目📺， 自动收集整理。</div>
<br>
<p align="center">
  <a href="https://github.com/alantang1977/iptv_SuperD/releases">
    <img src="https://img.shields.io/github/v/release/alantang1977/iptv_SuperD" />
  </a>
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/python-%20%3D%203.13-47c219" />
  </a>
  <a href="https://github.com/alantang1977/iptv_SuperD/releases">
    <img src="https://img.shields.io/github/downloads/alantang1977/iptv_SuperD/total" />
  </a>
  <a href="https://github.com/alantang1977/iptv_SuperD">
    <img src="https://img.shields.io/github/stars/alantang1977/iptv_SuperD" />
  </a>
  <a href="https://github.com/alantang1977/iptv_SuperD/fork">
    <img src="https://img.shields.io/github/forks/alantang1977/iptv_SuperD" />
  </a>
</p>

# IPTV

IPTV相关，适合个性化自定义...

* 自动收集整理
* 优先使用高频的直播源
* 优化的EPG文件尺寸，过滤掉直播源中不存在的频道
* 自动生成 [dist](https://github.com/alantang1977/iptv_SuperD/tree/dist)

## 使用

### 直接调用

```txt
https://raw.githubusercontent.com/alantang1977/iptv_SuperD/dist/live.m3u
```

```txt
https://raw.githubusercontent.com/alantang1977/iptv_SuperD/dist/live.txt
```

```txt
https://raw.githubusercontent.com/alantang1977/iptv_SuperD/dist/live-ipv4.m3u
```

```txt
https://raw.githubusercontent.com/alantang1977/iptv_SuperD/dist/live-ipv4.txt
```

```txt
https://raw.githubusercontent.com/alantang1977/iptv_SuperD/dist/epg.xml
```

```txt
https://raw.githubusercontent.com/alantang1977/iptv_SuperD/dist/epg.xml.gz
```

*注意: EPG为了减少文件大小，已经过处理，仅包含`channel.txt`中的频道（也就是生成的直播源中所包含的频道）数据，因此不通用，应与本项目生成的直播源文件配合使用*

### 手动生成

```shell
pip install -r requirements.txt
# m3u txt
python iptv.py
# epg
python epg.py
```

## 其它

* 直播源来自网络收集
* EPG来自 http://epg.51zmt.top:8000/
* 台标大部分来自 https://github.com/wanglindl/TVlogo
