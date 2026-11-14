/* Publish: anyone can edit and test a page; pushing it live needs proof that you own the site. Until a site is
   connected, the sheet hands over the edited HTML. */

import { S, h, hostOf, pathOf, toast, openSheet } from '../core.js';
import { ED, draftFor, syncFromFrame } from './editor.js';

export function publishButton(draft) {
  return h('button', { class: 'btn btn-block', type: 'button', disabled: !draft.text, onClick: () => openPublish(draft) }, 'Publish');
}

export function openPublish(draft) {
  syncFromFrame();
  const domain = hostOf(draft.url);
  const owned = S.report && hostOf(`https://${S.report.site}`) === domain;
  openSheet((sheet, closeBtn) => {
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: `Publish to ${domain}` }), h('div', { class: 'sub', text: draft.url })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    body.append(h('div', { class: 'tile white' },
      h('span', { class: 'label label-medium', text: 'Needs ownership' }),
      h('p', { class: 'reason', text: owned ? `Pushing a change live needs you to prove you own ${domain} and connect where it's hosted. Connecting a site is coming next.` : `Anyone can edit and test this page. Pushing it live needs you to own ${domain}.` }),
      h('p', { class: 'caveat', text: 'Until then, take the edited page with you. The file is the page HTML with your edits and without its scripts, so copy your changes into the real page rather than replacing it.' })));
    const download = h('button', { class: 'btn btn-dark btn-large', type: 'button' }, 'Download HTML');
    download.addEventListener('click', () => {
      const blob = new Blob([draftFor(ED.refs.page).text], { type: 'text/html' });
      const a = h('a', { href: URL.createObjectURL(blob), download: `${(pathOf(draft.url).replace(/^\/|\/$/g, '').replace(/\//g, '-') || 'index')}.html` });
      document.body.append(a);
      a.click();
      setTimeout(() => {
        URL.revokeObjectURL(a.href);
        a.remove();
      }, 500);
    });
    const copy = h('button', { class: 'btn btn-large', type: 'button' }, 'Copy HTML');
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(draftFor(ED.refs.page).text);
        toast('Copied the edited HTML.');
      } catch (_err) {
        toast('Copy is blocked here. Use Download instead.');
      }
    });
    body.append(h('div', { class: 'form-actions' }, download, copy));
    sheet.append(body);
  });
}
