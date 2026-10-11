export interface CatalogModel {
  key: string;
  name: string;
  fileName: string;
  url: string;
  sizeBytes: number;
  sha256: string;
  license: string;
  licenseUrl: string;
  sourceUrl: string;
  role: string;
}

/** Pinned primary desktop model entry. Android renderer compatibility is not established yet. */
export const CATALOG_MODELS: CatalogModel[] = [{
  key: 'noobai-xl-primary',
  name: 'NoobAI-XL v1.1 · primary SDXL checkpoint',
  fileName: 'NoobAI-XL-v1.1.safetensors',
  url: 'https://huggingface.co/Laxhar/noobai-XL-1.1/resolve/814a274af2b8097c0828819d561ec74c7d0c6cea/NoobAI-XL-v1.1.safetensors',
  sizeBytes: 7105349958,
  sha256: '6681e8e4b134c81f16533acedb0d406d7e5e366e1624b4105178c64d00b05d51',
  license: 'FAIR AI Public License 1.0-SD',
  licenseUrl: 'https://freedevproject.org/faipl-1.0-sd/',
  sourceUrl: 'https://huggingface.co/Laxhar/noobai-XL-1.1',
  role: 'Primary checkpoint used by the desktop manga workflow.',
}];
