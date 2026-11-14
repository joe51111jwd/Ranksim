// Every selector the end-to-end suite uses, in one place. When the UI moves (the P0 split, new screens),
// change it here and nowhere else.
export const SEL = {
  password: '#pw',
  loginSubmit: 'form button[type=submit]',
  loginError: '.form-error',
  signOut: 'button[aria-label="Sign out"]',

  homeInput: '.composer input',
  homeSubmit: '.composer button[type=submit]',
  homeDemo: '.demo-entry',
  homeError: '.home-error:not([hidden])',
  siteCard: '.site-card',

  progress: '.progress-tile',
  progressStep: '.progress-tile .step',

  nav: 'nav[aria-label="Sections"]',
  navLink: (label) => `nav[aria-label="Sections"] a:has-text("${label}")`,
  tile: '.bento .tile',
  chart: 'svg.rc',
  chartTitle: 'svg.rc > title',
  opportunity: 'button.opp',
  serpYou: '.serp .rowcard.you',
  serpEdit: '.serp .rowcard a[aria-label*="in the editor"]',
  loading: 'main .boot, main .skeleton',
  offline: '.offline',
  skeleton: (kind) => `main .skeleton.sk-${kind}`,

  gscSection: 'section.tile:has(h2:text-is("Google Search Console"))',
  gscTile: '[data-gsc="overview"]',

  shareSection: 'section.tile:has(h2:text-is("Client report"))',
  shareLabel: 'input[aria-label="Link label"]',
  shareCreate: 'button:has-text("Create link")',
  shareRow: '.share-row',
  shareUrl: '.share-row code.share-url',
  shareOff: '.share-row button:has-text("Turn off")',

  issueRow: '.rowcard',
  sheet: '.sheet[role=dialog]',

  searchRow: '.qrow',
  serpRow: '.serp .rowcard',

  competitorRow: '.tile .rowcard',

  editorFrame: 'iframe[title="Editable page"]',
  testButton: '#test-btn',
  verdict: '.verdict',

  settingsSearches: 'section.tile:has(h2:text-is("Searches")) textarea',
  saveSearches: 'button:has-text("Save searches")',
  refreshNow: 'button:has-text("Refresh now")',
  toast: '#toast',
};
