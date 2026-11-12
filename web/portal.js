const viewport = document.querySelector('#viewport');
const content = document.querySelector('#content');
const detail = document.querySelector('#detail');
let entries = [];
let revision = 0;
let rendering = 0;
let changed = false;
const pool = Array.from({length: 5}, (_, index) => {
  const row = document.createElement('div');
  row.id = `recycled-${index}`;
  row.className = 'row';
  content.append(row);
  return row;
});

function draw() {
  rendering += 1;
  const first = Math.floor(viewport.scrollTop / 52);
  content.style.height = `${entries.length * 52}px`;
  pool.forEach((node, slot) => {
    const logical = first + slot;
    const entry = entries[logical];
    node.hidden = !entry;
    node.replaceChildren();
    if (!entry) return;
    node.style.top = `${logical * 52}px`;
    node.dataset.rowIndex = logical;
    const name = document.createElement('span');
    name.textContent = `${entry.name} | ${entry.country} | ${entry.booth}`;
    const button = document.createElement('button');
    button.textContent = '详情';
    button.dataset.reference = entry.reference;
    button.addEventListener('click', () => {
      detail.dataset.reference = entry.reference;
      detail.querySelector('h2').textContent = entry.name;
      detail.querySelector('p').textContent = `${entry.reference}：${entry.description}`;
      detail.showModal();
    });
    node.append(name, button);
  });
  viewport.dataset.revision = revision;
  viewport.dataset.total = entries.length;
  viewport.dataset.state = 'ready';
  document.querySelector('#status').textContent = `当前代际 ${revision}，共 ${entries.length} 行`;
  if (new URLSearchParams(location.search).has('mutate') && rendering >= 4 && !changed) {
    changed = true;
    load('/advance');
  }
}

async function load(path) {
  viewport.dataset.state = 'loading';
  const response = await fetch(path);
  const data = await response.json();
  entries = data.rows;
  revision = data.revision;
  draw();
}

viewport.addEventListener('scroll', draw);
document.querySelector('#refresh').addEventListener('click', () => load('/advance'));
document.querySelector('#close').addEventListener('click', () => detail.close());
load('/seed');
