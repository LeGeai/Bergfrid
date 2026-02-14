import type { APIRoute } from 'astro';
import { directus } from '../lib/directus';
import { readItems } from '@directus/sdk';
import { SITE_TITLE, API_URL } from '../consts';

export const prerender = false;

export const GET: APIRoute = async ({ url }) => {
  const lang = url.searchParams.get('lang') || 'fr-FR';
  const SITE_URL = url.origin;

  try {
    // Récupérer tous les fils/threads publiés avec plus de détails
    const threads = await directus.request(
      readItems('threads', {
        filter: { status: { _eq: 'published' } },
        sort: ['-date_updated', '-date_created'],
        fields: ['id', 'slug', 'date_created', 'date_updated', 'cover_image', 'threads_translations.*'],
        limit: 100
      })
    );

    function getTrad(thread: any, langCode: string) {
      const trads = thread.threads_translations || [];
      if (!trads || trads.length === 0) return { title: 'Fil sans titre', description: '', slug: '#' };

      let t = trads.find((tr: any) => tr.languages_code === langCode);
      if (!t) {
        const shortLang = langCode.split('-')[0];
        t = trads.find((tr: any) => tr.languages_code && tr.languages_code.startsWith(shortLang));
      }
      if (!t) t = trads.find((tr: any) => tr.languages_code === 'en-US');
      if (!t) t = trads[0];

      return t || { title: 'Erreur', description: '', slug: '#' };
    }

    // Construire le flux RSS enrichi
    const rssItems = threads.map((thread: any) => {
      const trad = getTrad(thread, lang);
      const pubDate = new Date(thread.date_created).toUTCString();
      const modDate = thread.date_updated ? new Date(thread.date_updated).toUTCString() : pubDate;
      const link = `${SITE_URL}/thread/${trad.slug}`;
      const imageUrl = thread.cover_image ? `${API_URL}/assets/${thread.cover_image}` : '';

      return `
    <item>
      <title><![CDATA[${trad.title}]]></title>
      <description><![CDATA[${trad.description || 'Dossier géopolitique suivi en temps réel par Bergfrid.'}]]></description>
      <link>${link}</link>
      <guid isPermaLink="true">${link}</guid>
      <pubDate>${pubDate}</pubDate>
      <dc:date>${new Date(thread.date_created).toISOString()}</dc:date>
      ${thread.date_updated ? `<dc:modified>${new Date(thread.date_updated).toISOString()}</dc:modified>` : ''}
      <dc:creator>Rédaction Bergfrid</dc:creator>
      <dc:language>${lang.split('-')[0]}</dc:language>
      <dc:type>Series</dc:type>
      <category>Dossier Géopolitique</category>
      <category>Analyse Stratégique</category>
      ${imageUrl ? `
      <enclosure url="${imageUrl}" type="image/jpeg" />
      <media:content url="${imageUrl}" medium="image" type="image/jpeg">
        <media:title type="plain"><![CDATA[${trad.title}]]></media:title>
        <media:description type="plain"><![CDATA[${trad.description || ''}]]></media:description>
      </media:content>
      <media:thumbnail url="${imageUrl}" />
      ` : ''}
    </item>`;
    }).join('');

    const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>${SITE_TITLE} - Dossiers Géopolitiques</title>
    <link>${SITE_URL}</link>
    <description>Dossiers géopolitiques suivis en temps réel : analyses approfondies, suivis de crises et documentation stratégique par Bergfrid.</description>
    <language>${lang}</language>
    <copyright>Copyright ${new Date().getFullYear()} Bergfrid</copyright>
    <managingEditor>redaction@bergfrid.com (Rédaction Bergfrid)</managingEditor>
    <webMaster>tech@bergfrid.com (Équipe Technique Bergfrid)</webMaster>
    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
    <atom:link href="${SITE_URL}/rss-threads.xml?lang=${lang}" rel="self" type="application/rss+xml" />
    <image>
      <url>${SITE_URL}/favicon.png</url>
      <title>${SITE_TITLE} - Dossiers</title>
      <link>${SITE_URL}</link>
    </image>
    ${rssItems}
  </channel>
</rss>`;

    return new Response(rss, {
      headers: {
        'Content-Type': 'application/xml; charset=utf-8',
        'Cache-Control': 'public, max-age=1800, stale-while-revalidate=3600'
      }
    });

  } catch (error) {
    console.error('RSS Threads Error:', error);
    return new Response('Error generating RSS feed', { status: 500 });
  }
};
