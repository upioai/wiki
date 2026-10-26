/* 参考案例集两个页面共用的小工具。页面本身不带任何账号数据，全部从 db/ 读。 */
var CR = (function () {
  var BASE = '/akke/case-refs/';
  var API = '/api/case-refs/labels';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function getJSON(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(r.status + ' ' + url);
      return r.json();
    });
  }

  function param(name) {
    var m = new RegExp('[?&]' + name + '=([^&#]*)').exec(location.search);
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : '';
  }

  function acctURL(slug) {
    return BASE + 'account?s=' + encodeURIComponent(slug);
  }

  function fail(el, msg) {
    el.innerHTML = '<section class="detail-hero"><div class="wrap"><span class="eyebrow">参考案例集</span>'
      + '<h1>页面没有加载出来</h1><p class="lede">' + esc(msg) + '</p>'
      + '<p class="intro-copy"><a href="' + BASE.replace(/\/$/, '') + '">返回参考案例集</a></p></div></section>';
  }

  /* 标注：直接提交到共用标注库，同一条视频以最后一次为准。不记录是谁标的。 */
  var labels = {
    load: function (slug) {
      return getJSON(API + '?s=' + encodeURIComponent(slug)).then(function (d) {
        return d.labels || {};
      });
    },
    save: function (row) {
      return fetch(API, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(row)
      }).then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) {
          throw new Error(d.error || ('提交失败（' + r.status + '）'));
        });
        return r.json();
      });
    },
    review: function (slug, status, note) {
      return labels.save({ kind: 'review', slug: slug, status: status, note: note });
    },
    getReview: function (slug) {
      return getJSON(API + '?review=1&s=' + encodeURIComponent(slug)).then(function (d) { return d.review; });
    },
    remove: function (slug, id) {
      return fetch(API + '?s=' + encodeURIComponent(slug) + '&id=' + encodeURIComponent(id), { method: 'DELETE' })
        .then(function (r) { if (!r.ok) throw new Error('删除失败（' + r.status + '）'); return r.json(); });
    }
  };

  return { BASE: BASE, esc: esc, getJSON: getJSON, param: param, acctURL: acctURL, fail: fail, labels: labels };
})();
