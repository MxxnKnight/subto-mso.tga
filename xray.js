const Xray = require('x-ray');
const x = Xray().concurrency(5).throttle(5, '1s'); // Be respectful to the server

const translatedBy = '#release-details-table tbody tr:nth-child(3) td:nth-child(2) a';

x('https://malayalamsubtitles.org/releases/', 'article.loop-entry', [
    {
        title: 'h2.entry-title a',
        link: 'h2.entry-title a@href',
        post: x('h2.entry-title a@href', {
            title: 'h1#release-title',
            posterMalayalam: 'figure#release-poster img@src',
            descriptionMalayalam: 'div#synopsis',
            imdbURL: 'a#imdb-button@href',
            srtURL: 'a#download-button@data-downloadurl',
            translatedBy: {
                name: translatedBy,
                url: translatedBy + '@href',
            },
        }),
    },
]).paginate('a.next.page-numbers@href')
  .limit(3) // Let's just grab a few pages for testing purposes
  .write('results.json');
