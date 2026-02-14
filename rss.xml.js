// src/pages/rss.xml.js
import rss from '@astrojs/rss';
import { SITE_TITLE, API_URL } from '../consts';
import { directus } from '../lib/directus';
import { readItems } from '@directus/sdk';
import sanitizeHtml from 'sanitize-html';

export async function GET() {
    // URL DU SITE (depuis le .env du serveur, ou fallback localhost)
    const SITE_URL = import.meta.env.SITE_URL || 'https://bergfrid.com';

    let articles = [];
    try {
        articles = await directus.request(
            readItems('articles', {
                sort: ['-date_created'],
                fields: ['*', 'translations.*', 'user_created.pseudo', 'tags', 'map'],
                filter: { status: { _eq: 'published' } },
                limit: 50 // Augmenté pour Google News
            })
        );
    } catch (e) {
        console.error("RSS Error:", e);
        return new Response('Database Error', { status: 500 });
    }

    const getTrad = (item) => {
        const trads = item.translations;
        if (!trads || trads.length === 0) return null;
        let t = trads.find(tr => tr.languages_code === 'fr-FR');
        if (!t) t = trads[0];
        return t;
    };

    // Helper pour convertir un tag en hashtag propre (PascalCase, sans accents, sans espaces)
    const toHashtag = (tag) => {
        const cleaned = tag
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '') // supprime accents
            .split(/[\s''-]+/) // découpe par espaces, apostrophes, tirets
            .filter(Boolean)
            .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()) // PascalCase
            .join('')
            .replace(/[^a-zA-Z0-9]/g, ''); // supprime tout caractère spécial restant
        return cleaned ? '#' + cleaned : '';
    };

    // Helper pour extraire un résumé du contenu HTML
    const extractSummary = (content, maxLength = 280) => {
        if (!content) return '';
        const plainText = content.replace(/<[^>]*>/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
        return plainText.length > maxLength
            ? plainText.substring(0, maxLength) + '...'
            : plainText;
    };

    return rss({
        title: SITE_TITLE + ' - Actualités Géopolitiques',
        description: "Intelligence géopolitique, analyses stratégiques et données en temps réel. Suivez les conflits, les crises internationales et les enjeux de sécurité mondiale.",
        site: SITE_URL,
        xmlns: {
            // Google News et standards RSS modernes
            media: 'http://search.yahoo.com/mrss/',
            content: 'http://purl.org/rss/1.0/modules/content/',
            dc: 'http://purl.org/dc/elements/1.1/',
            atom: 'http://www.w3.org/2005/Atom',
            geo: 'http://www.w3.org/2003/01/geo/wgs84_pos#',
            bergfrid: 'https://bergfrid.com/ns/rss'
        },
        customData: `
            <language>fr</language>
            <copyright>Copyright ${new Date().getFullYear()} Bergfrid</copyright>
            <managingEditor>redaction@bergfrid.com (Rédaction Bergfrid)</managingEditor>
            <webMaster>tech@bergfrid.com (Équipe Technique Bergfrid)</webMaster>
            <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
            <atom:link href="${SITE_URL}/rss.xml" rel="self" type="application/rss+xml" />
            <image>
                <url>${SITE_URL}/favicon.png</url>
                <title>${SITE_TITLE}</title>
                <link>${SITE_URL}</link>
            </image>
        `,
        items: articles.map((post) => {
            const t = getTrad(post);
            if (!t) return null;

            const author = post.user_created?.pseudo || 'Rédaction Bergfrid';
            const imageUrl = post.image ? `${API_URL}/assets/${post.image}` : null;
            const articleUrl = `${SITE_URL}/blog/${t.slug}`;

            // Générer un résumé riche (priorité: social_summary > summary > extrait du contenu)
            const summary = t.social_summary || t.summary || extractSummary(t.content);

            // Nettoyage HTML pour contenu complet
            const cleanContent = t.content ? sanitizeHtml(t.content, {
                allowedTags: sanitizeHtml.defaults.allowedTags.concat(['img', 'figure', 'figcaption']),
                allowedAttributes: {
                    ...sanitizeHtml.defaults.allowedAttributes,
                    img: ['src', 'alt', 'title', 'width', 'height'],
                    a: ['href', 'title', 'rel', 'target']
                }
            }) : '';

            // Tags, catégories et hashtags pour les bots sociaux
            const tags = Array.isArray(post.tags) ? post.tags : [];
            const primaryCategory = post.map || tags[0] || 'Géopolitique';
            const hashtags = tags.map(toHashtag).filter(Boolean);
            const hashtagLine = hashtags.length > 0 ? '\n\n' + hashtags.join(' ') : '';

            return {
                title: t.title,
                pubDate: new Date(post.date_created),
                link: articleUrl,
                description: summary + hashtagLine,
                content: cleanContent,
                author: author,
                categories: [primaryCategory, ...tags],
                customData: `
                    <guid isPermaLink="true">${articleUrl}</guid>
                    <bergfrid:id>${post.id}</bergfrid:id>
                    <dc:creator>${author}</dc:creator>
                    <dc:date>${new Date(post.date_created).toISOString()}</dc:date>
                    ${post.date_updated ? `<dc:modified>${new Date(post.date_updated).toISOString()}</dc:modified>` : ''}
                    <dc:language>fr</dc:language>
                    <dc:rights>Copyright ${new Date(post.date_created).getFullYear()} Bergfrid</dc:rights>
                    <dc:type>Text</dc:type>
                    <dc:format>text/html</dc:format>
                    ${primaryCategory ? `<dc:subject>${primaryCategory}</dc:subject>` : ''}
                    ${imageUrl ? `
                        <media:content url="${imageUrl}" medium="image" type="image/jpeg">
                            <media:title type="plain">${t.title}</media:title>
                            <media:description type="plain">${summary}</media:description>
                        </media:content>
                        <media:thumbnail url="${imageUrl}" />
                        <enclosure url="${imageUrl}" type="image/jpeg" />
                    ` : ''}
                    ${post.map ? `<geo:country>${post.map}</geo:country>` : ''}
                    ${t.social_summary ? `<bergfrid:social_summary><![CDATA[${t.social_summary}]]></bergfrid:social_summary>` : ''}
                    ${hashtags.length > 0 ? `<bergfrid:hashtags>${hashtags.join(' ')}</bergfrid:hashtags>` : ''}
                `
            };
        }).filter(Boolean),
        stylesheet: '/rss-styles.xsl'
    });
}