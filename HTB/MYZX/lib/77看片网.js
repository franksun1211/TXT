var rule = {
    title: '77看片网',
    host: 'https://www.77kkpp.com',
    url: '/vod-type-id-fyclass-pg-fypage.html',
    searchUrl: '/index.php/ajax/suggest?mid=1&wd=**',
    detailUrl: '/vod/fyid.html',
    searchable: 2,
    quickSearch: 0,
    headers: {
        'User-Agent': 'MOBILE_UA',
    },
    timeout: 5000,
    class_parse: '.sj-nav-down-2 li;a&&Text;a&&href;id-(\\d+)',
    cate_exclude: '',
    play_parse: true,
    lazy:`js:
  let html = request(input);
  let hconf = html.match(/r player_.*?=(.*?)</)[1];
  let json = JSON5.parse(hconf);
  let url = json.url;
  if (json.encrypt == '1') {
    url = unescape(url);
  } else if (json.encrypt == '2') {
    url = unescape(base64Decode(url));
  }
  if (/\\.(m3u8|mp4|m4a|mp3)/.test(url)) {
    input = {
      parse: 0,
      jx: 0,
      url: url,
    };
  } else {
    input;
  }`,
    double: true,
    推荐: '*',
    一级: 'body&&.p2.m1;a&&title;.lazy&&data-original;.other&&Text;a&&href',
    二级: {
        title: '.ct.mb&&dt&&Text;vod_type',
        img: '.ct.mb&&.lazy&&data-original',
        desc: '主要信息;.ct.mb&&dd:eq(1)--span&&Text;.ct.mb&&dd:eq(2)--span&&Text;.ct.mb&&dt:eq(1)--span&&Text;.ct.mb&&dd--span&&Text',
        content: '.ct.mb&&.desc--span&&Text',
        tabs: '.playfrom li',
        lists: '.videourl:eq(#id) li',
        tab_text: 'body&&Text',
        list_text: 'body&&Text',
        list_url: 'a&&href',
    },
    搜索:'json:list;name;pic;en;id',
}