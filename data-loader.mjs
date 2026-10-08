/**
 * Reusable data layer. No DOM access and no external packages.
 *
 * import { ArxivDataset } from './data-loader.mjs';
 * const data = new ArxivDataset('./data/arxiv/');
 * await data.loadIndex();
 * const papers = await data.load({ from: '2024-01', to: '2024-12' });
 * // data.manifest.months describes available snapshots; data.records is
 * // the last fully loaded selection. load() without dates loads all months.
 * // Optional: signal (AbortSignal), onProgress({loaded, total, month}).
 */

async function readJSON(url, signal) {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(signal.reason);
  if (signal?.aborted) forwardAbort();
  else signal?.addEventListener('abort', forwardAbort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
  try {
    const response = await fetch(url, { signal: controller.signal, cache: 'no-cache' });
    if (!response.ok) {
      const error = new Error(`Cannot read ${url.pathname} (HTTP ${response.status}). Run make index after fetching data.`);
      error.status = response.status;
      throw error;
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    // Fetch may already have decoded a server's Content-Encoding: gzip.
    // Check the actual bytes to avoid decompressing the same content twice.
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
      if (typeof DecompressionStream === 'undefined') {
        throw new Error('This browser cannot unpack gzip. Run make unpack and reload the index.');
      }
      const decoded = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      return JSON.parse(await new Response(decoded).text());
    }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch (error) {
    if (timedOut) throw new Error(`Reading ${url.pathname} timed out after 15 seconds. Check that the local server is running.`);
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

const monthPattern = /^\d{4}-(0[1-9]|1[0-2])$/;
// arxiv.org/category_taxonomy: express official aliases as math codes.
const mathAliases = { 'cs.IT': 'math.IT', 'math-ph': 'math.MP', 'cs.NA': 'math.NA', 'stat.TH': 'math.ST' };

// Include gaps: a missing snapshot does not remove a calendar month.
export function calendarMonths(from, to) {
  if (!monthPattern.test(from) || !monthPattern.test(to) || from > to) {
    throw new Error('Choose a valid month range.');
  }
  const result = [];
  let [year, month] = from.split('-').map(Number);
  while (true) {
    const value = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
    if (value > to) break;
    result.push(value);
    if (++month === 13) { month = 1; year++; }
  }
  return result;
}

/** Pool corresponding calendar months across the loaded years. Missing
 * snapshots do not contribute zero observations to the average. */
export function groupCalendarMonths(months) {
  const groups = Array.from({ length: 12 }, (_, i) => ({
    month: String(i + 1).padStart(2, '0'), count: 0, availableMonths: 0, expectedMonths: 0
  }));
  for (const item of months) {
    const group = groups[Number(item.month.slice(5, 7)) - 1];
    group.expectedMonths++;
    if (item.count !== null) { group.count += item.count; group.availableMonths++; }
  }
  return groups.map(group => ({ ...group,
    count: group.availableMonths ? group.count : null,
    average: group.availableMonths ? group.count / group.availableMonths : null
  }));
}

export class ArxivDataset {
  constructor(directory = './data/arxiv/', baseURL = globalThis.location?.href) {
    this.directory = new URL(directory.replace(/\/?$/, '/'), baseURL);
    this.manifest = null;
    this.records = [];
    this.cache = new Map();
  }

  async loadIndex({ signal } = {}) {
    const manifest = await readJSON(new URL('manifest.json', this.directory), signal);
    if (manifest.schema_version !== 1 || !Array.isArray(manifest.months)) {
      throw new Error('Unsupported data index. Regenerate it with make index.');
    }
    const seen = new Set();
    for (const item of manifest.months) {
      if (!monthPattern.test(item.month) || seen.has(item.month) ||
          !Number.isSafeInteger(item.count) || item.count < 0 ||
          typeof item.query !== 'string' || (!item.unpacked && !item.packed)) {
        throw new Error('Malformed monthly entry in the data index.');
      }
      for (const name of [item.unpacked, item.packed].filter(Boolean)) {
        if (name !== `${item.month}.json` && name !== `${item.month}.json.gz`) {
          throw new Error('Unexpected monthly filename in the data index.');
        }
      }
      seen.add(item.month);
    }
    // Each selection must have consistent coverage; queries cannot be mixed.
    if (new Set(manifest.months.map(item => item.query)).size > 1) {
      throw new Error('This directory mixes different queries. Keep each query in its own DATA_DIR.');
    }
    manifest.months.sort((a, b) => a.month.localeCompare(b.month));
    this.manifest = manifest;
    this.records = [];
    this.cache.clear();
    return manifest;
  }

  async load({ from, to, signal, onProgress = () => {} } = {}) {
    if (!this.manifest) await this.loadIndex({ signal });
    if ((from && !monthPattern.test(from)) || (to && !monthPattern.test(to)) ||
        (from && to && from > to)) {
      throw new Error('Choose a valid month range.');
    }
    const selected = this.manifest.months.filter(item =>
      (!from || item.month >= from) && (!to || item.month <= to));
    const records = [];
    const seen = new Set();
    for (const [index, item] of selected.entries()) {
      signal?.throwIfAborted();
      let snapshot = this.cache.get(item.month);
      if (!snapshot) {
        // Unpacked JSON is preferred when the maintenance index advertises
        // it. Otherwise use gzip with the browser's native decompressor.
        snapshot = await readJSON(new URL(item.unpacked || item.packed, this.directory), signal);
        if (snapshot.schema_version !== 1 || snapshot.month !== item.month ||
            snapshot.query !== item.query || snapshot.fetched_at !== item.fetched_at ||
            !Array.isArray(snapshot.papers) || snapshot.papers.length !== item.count) {
          throw new Error(`Snapshot/index mismatch for ${item.month}. Run make index and retry.`);
        }
        for (const paper of snapshot.papers) {
          if (typeof paper.id !== 'string' || !paper.id ||
              typeof paper.title !== 'string' || typeof paper.submitted !== 'string' ||
              !paper.submitted.startsWith(item.month + '-') ||
              !Array.isArray(paper.categories) || !paper.categories.every(c => typeof c === 'string')) {
            throw new Error(`Malformed paper metadata in ${item.month}.`);
          }
        }
        this.cache.set(item.month, snapshot);
      }
      for (const paper of snapshot.papers) {
        if (seen.has(paper.id)) throw new Error(`Duplicate arXiv ID: ${paper.id}`);
        seen.add(paper.id);
        records.push(paper);
      }
      onProgress({ loaded: index + 1, total: selected.length, month: item.month });
    }
    signal?.throwIfAborted();
    this.records = records;
    return records;
  }

  /** Read actual files to sum monthly submissions. Missing files are reported,
   * not counted as zero; corrupt files and HTTP errors other than 404 fail.
   * Returns total, monthly counts, primary-category counts and missing months.
   * With retainRecords: true, also sets this.records after a successful read.
   * Does not trust stale index counts or caches.
   */
  async summarize({ from, to, signal, retainRecords = false, onProgress = () => {} } = {}) {
    if (!this.manifest) await this.loadIndex({ signal });
    const indexed = this.manifest.months;
    if (!indexed.length) throw new Error('No monthly snapshots are available.');
    from ??= indexed[0].month;
    to ??= indexed.at(-1).month;
    if (from < indexed[0].month || to > indexed.at(-1).month) {
      throw new Error('Choose a range within the indexed period.');
    }
    const selected = calendarMonths(from, to);
    const items = new Map(indexed.map(item => [item.month, item]));
    const months = [];
    const categoryTotals = Object.create(null);
    const authorsByCategory = Object.create(null);
    let unknownAuthorCounts = 0;
    const records = [];
    const seen = new Set();
    let total = 0;
    for (const [i, month] of selected.entries()) {
      signal?.throwIfAborted();
      const item = items.get(month);
      let snapshot;
      if (item) {
        for (const filename of [item.unpacked, item.packed].filter(Boolean)) {
          try {
            snapshot = await readJSON(new URL(filename, this.directory), signal);
            break;
          } catch (error) { if (error.status !== 404) throw error; }
        }
      }
      if (snapshot !== undefined) {
        if (!snapshot || snapshot.schema_version !== 1 || snapshot.month !== month ||
            snapshot.query !== item.query || !Array.isArray(snapshot.papers)) {
          throw new Error(`Malformed snapshot for ${month}.`);
        }
        const categories = Object.create(null);
        const authorStats = Object.create(null);
        for (const paper of snapshot.papers) {
          if (typeof paper.id !== 'string' || !paper.id || seen.has(paper.id) ||
              typeof paper.submitted !== 'string' || !paper.submitted.startsWith(month + '-')) {
            throw new Error(`Invalid submission or duplicate ID in ${month}.`);
          }
          seen.add(paper.id);
          const primary = typeof paper.primary_category === 'string' && paper.primary_category.trim()
            ? paper.primary_category.trim() : 'Unclassified';
          const category = mathAliases[primary] || primary;
          categories[category] = (categories[category] || 0) + 1;
          categoryTotals[category] = (categoryTotals[category] || 0) + 1;
          const authorRow = authorsByCategory[category] ??= { counts: [0, 0, 0, 0, 0], unknown: 0 };
          const authorCount = paper.author_count ?? (Array.isArray(paper.authors) ? paper.authors.length : null);
          const stats = authorStats[category] ??= { sum: 0, known: 0, unknown: 0 };
          if (Number.isSafeInteger(authorCount) && authorCount >= 1) {
            authorRow.counts[Math.min(authorCount, 5) - 1]++;
            stats.sum += authorCount;
            stats.known++;
            if (!Number.isSafeInteger(stats.sum)) throw new Error('Author count sum is too large.');
          } else {
            authorRow.unknown++;
            unknownAuthorCounts++;
            stats.unknown++;
          }
          if (retainRecords) records.push(paper);
        }
        total += snapshot.papers.length;
        if (!Number.isSafeInteger(total)) throw new Error('Submission count is too large.');
        months.push({ month, count: snapshot.papers.length, categories, authorStats });
      } else {
        months.push({ month, count: null, categories: null, authorStats: null });
      }
      onProgress({ loaded: i + 1, total: selected.length, month });
    }
    const missingMonths = months.filter(item => item.count === null).map(item => item.month);
    signal?.throwIfAborted();
    if (retainRecords) this.records = records;
    return { total, months, categoryTotals, authorsByCategory, unknownAuthorCounts,
      missingMonths, loadedMonths: months.length - missingMonths.length };
  }
}
