
# arXiv Mathematics Activity

A lightweight, responsive website for exploring aggregate arXiv mathematics submissions.

Metadata is downloaded during repository maintenance. The website reads those local snapshots and performs all analysis in the browser. It does not contact arXiv.

No JavaScript frameworks, package installation, database, or build step are required.

## Features

- Select an inclusive range of months from the available data.
- Show total submissions and monthly counts with percentages.
- Combine years to compare calendar months, such as May versus June.
- Show the relative proportions of primary categories.
- Toggle mathematics subcategories and plot monthly submission counts.
- Switch the graph to average authors per article.
- Show author-count distributions by category: 1, 2, 3, 4, and 5+.
- Color author-count cells from white at 0% to strong pink at 100%.

Graphs use smoothed lines through the monthly observations. Missing months appear as gaps.

## Requirements

For downloading metadata and serving the website:

- GNU Make
- Python 3.9 or newer
- A Unix-like environment, such as Linux, macOS, or WSL

For viewing:

- A modern browser
- Native `DecompressionStream` support when using gzip snapshots

Python uses only its standard library. Unpacked JSON can be used when browser gzip support is unavailable.

## Quick start

Run these commands from the repository root:

```sh
make fetch-metadata
make serve
```

Open <http://localhost:8000>.

The default download covers the current calendar year and the previous two calendar years, through the current month. The current month is incomplete.

Choose the start and end months, then press **SHOW**. Select subcategory buttons and press the graph's **SHOW** button to update its lines.

The repository does not require bundled data. Fetch metadata before using an empty checkout.

## Downloading and updating metadata

Download five calendar years:

```sh
make fetch-metadata YEARS=5
```

Download an explicit range:

```sh
make fetch-metadata START_YEAR=2022 END_YEAR=2025
```

Refresh all months in the requested range:

```sh
make fetch-metadata START_YEAR=2022 END_YEAR=2025 REFRESH=1
```

By default, existing historical snapshots are reused. The current and previous month are refreshed.

Downloads run sequentially, with at least 3.1 seconds between request starts and retries for temporary failures. Completed snapshots survive an interrupted download.

Run only one arXiv maintenance download at a time across your machines.

### Category query

The default query is `cat:math.*`. Restrict a new dataset to numerical analysis with:

```sh
make fetch-metadata QUERY='cat:math.NA'
```

A data directory must contain snapshots from a single query. Changing the query for existing snapshots requires a separate directory:

```sh
make fetch-metadata QUERY='cat:math.NA' DATA_DIR=data/numerical-analysis
```

The website always reads `data/arxiv`. Alternate directories are maintenance outputs and are not selectable in the interface.

## Data files

| File | Purpose |
| --- | --- |
| `data/arxiv/YYYY-MM.json.gz` | Compressed metadata for one submission month |
| `data/arxiv/YYYY-MM.json` | Optional unpacked copy |
| `data/arxiv/manifest.json` | Index of available snapshots |

Each paper record contains:

- arXiv identifier, without the version suffix
- Original submission and last-update timestamps
- Title
- Primary category and category list
- Number of author entries reported by arXiv

Snapshots also record the query, retrieval time, schema version, and metadata license.

Abstracts, author names, PDFs, and source files are not stored.

### Unpacking

Create ordinary JSON copies without making network requests:

```sh
make unpack
```

This preserves the compressed files and rebuilds the manifest. The website prefers an indexed unpacked copy when available; otherwise it reads and decompresses gzip in the browser.

After refreshing downloaded data, run `make unpack` again if you use unpacked copies.

### Rebuilding the index

After adding or deleting monthly files:

```sh
make index
```

`make serve` also rebuilds the index before starting the local server.

The manifest describes file availability; it is not a replacement for the monthly data. The website reads the selected snapshots to calculate results.

## Interpretation

### Submissions and categories

An article is counted once, in the month of its original submission. Revisions are not counted as new submissions.

The arXiv query can match cross-listed papers. Category statistics assign each retrieved paper to its primary category, with equivalent mathematics category aliases grouped together. A cross-listed paper can therefore contribute to a primary category outside mathematics.

Monthly percentages use the total number of articles in the loaded range.

### Combined years

**Combine years** pools January across the selected years, February across the selected years, and so on.

The table shows pooled counts, mean submissions per available month, percentages, and coverage. Missing snapshots are excluded from the mean rather than counted as zero.

### Authors

**Average authors** plots the arithmetic mean of valid author counts for each category and month. Actual counts above five are used in this calculation.

The author-count table groups counts of five or more into one column. Its percentages and background colors are calculated separately within each category, using articles with known author counts.

Missing or invalid author counts are excluded from author statistics and reported separately.

Snapshots created by an older fetcher may lack `author_count`. Refresh the relevant years with `REFRESH=1` to obtain it.

### Missing months

The date selectors include every month between the earliest and latest indexed months, including gaps.

Missing snapshots are reported explicitly. They contribute neither zero submissions nor zero authors, and graph lines break at those months.

## Repository structure

| File | Purpose |
| --- | --- |
| `index.html` | Interface, tables, and SVG graphs |
| `data-loader.mjs` | Browser data loading and aggregation |
| `fetch_arxiv.py` | Metadata download, unpacking, and indexing |
| `Makefile` | Maintenance and local-server commands |

The page exposes the loaded dataset as `window.arxivData` and the latest aggregate results as `window.arxivSummary`.

## Serving the website

Use HTTP rather than opening `index.html` directly through `file://`:

```sh
make serve
```

Use another port if needed:

```sh
make serve PORT=8080
```

For static hosting, publish the website files together with `data/arxiv/manifest.json` and the monthly snapshots it references.

The Python server is only needed for local development. Published analysis remains entirely in the browser.

## Troubleshooting

**No monthly snapshots available:** Run `make fetch-metadata`, then `make index`.

**Data loading fails:** Serve the repository over HTTP and check that the manifest and its referenced files are accessible.

**Gzip decompression is unsupported:** Run `make unpack`, then reload the page.

**Author statistics are unavailable:** Refresh the relevant years with `REFRESH=1`. Run `make unpack` afterward if using plain JSON.

**A download fails:** Rerun the same command. Previously completed monthly snapshots remain available.

## Licensing

The intended license for the website and maintenance code is MIT. Include the corresponding MIT license text in the repository's root `LICENSE` file when publishing.

arXiv descriptive metadata is provided under **CC0 1.0**. This metadata license is separate from the code license.

See [arXiv's API terms of use](https://info.arxiv.org/help/api/tou.html).

Licenses for article PDFs and source files are separate; those files are not included in this project.

