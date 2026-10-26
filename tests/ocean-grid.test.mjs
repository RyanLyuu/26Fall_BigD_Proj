import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';

const bundle = await build({
  entryPoints: ['src/lib/oceanGrid.ts'], bundle: true, write: false,
  platform: 'node', format: 'esm',
});
const { gridGeoJSON, demoGrid, eventBoundary } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const cell = { longitude: 122.125, latitude: 27.125, combination: 'thetao_o2_ph', eventIds: ['CE-001'], affectedFraction: 0.75, valid: true };
const frame = cells => ({ source: 'analysis', time: '2024-08-13', resolutionDegrees: 0.25, depthRange: [0, 50], cells });

test('uses service centers and full 0.25-degree cells without clipping or smoothing', () => {
  const feature = gridGeoJSON(frame([cell])).features[0];
  assert.deepEqual(feature.geometry.coordinates[0], [[122,27],[122.25,27],[122.25,27.25],[122,27.25],[122,27]]);
  assert.equal(feature.properties.color, '#414141');
});

test('missing support, zero affected fraction and pending fraction remain distinct', () => {
  assert.equal(gridGeoJSON(frame([{...cell,valid:false}])).features.length, 0);
  assert.equal(gridGeoJSON(frame([{...cell,affectedFraction:0}])).features.length, 0);
  const pending = gridGeoJSON(frame([{...cell,affectedFraction:null}])).features[0];
  assert.equal(pending.properties.fraction, null);
  assert.equal(pending.properties.color, '#a5a5a5');
});

test('rejects sub-quarter-degree grids and invalid measurements', () => {
  assert.throws(() => gridGeoJSON({...frame([cell]),resolutionDegrees:0.12}));
  assert.throws(() => gridGeoJSON(frame([{...cell,affectedFraction:1.1}])));
  assert.throws(() => gridGeoJSON(frame([{...cell,latitude:91}])));
});

test('each of the seven combinations has a separate color; selection is not part of the measurement', () => {
  const combinations = ['thetao','o2','ph','thetao_o2','thetao_ph','o2_ph','thetao_o2_ph'];
  const features = gridGeoJSON(frame(combinations.map(combination=>({...cell,combination})))).features;
  assert.equal(new Set(features.map(f=>f.properties.color)).size,7);
});

test('splits antimeridian cells into narrow polygons instead of spanning the world', () => {
  const features = gridGeoJSON(frame([{...cell,longitude:180}])).features;
  assert.equal(features.length,2);
  for (const f of features) {
    const longitudes = f.geometry.coordinates[0].map(p=>p[0]);
    assert.ok(Math.max(...longitudes)-Math.min(...longitudes)<=0.25);
    assert.ok(longitudes.every(lng=>lng>=-180&&lng<=180));
  }
});

test('synthetic fixture is reproducible, ocean-masked, exclusive and spans seven categories and depth fractions', () => {
  const demo = demoGrid();
  assert.equal(demo.source,'demo');
  assert.equal(demo.time,null);
  assert.ok(demo.cells.length>3000);
  assert.ok(demo.cells.every(c=>c.longitude*4%1===0.5&&c.latitude*4%1===0.5));
  assert.ok(demo.cells.every(c=>c.affectedFraction>0&&c.affectedFraction<=1));
  assert.equal(new Set(demo.cells.map(c=>c.combination)).size,7);
  assert.ok(new Set(demo.cells.map(c=>c.affectedFraction)).size>=8);
  assert.equal(new Set(demo.cells.map(c=>`${c.longitude},${c.latitude}`)).size,demo.cells.length);
  // Shanghai mainland must be excluded, and adjacent event pixels must differ.
  assert.ok(!demo.cells.some(c=>c.longitude===121.375&&c.latitude===31.125));
  const lookup=new Map(demo.cells.map(c=>[`${c.longitude},${c.latitude}`,c]));
  assert.ok(demo.cells.some(c=>{
    const neighbor=lookup.get(`${c.longitude+0.25},${c.latitude}`);
    return neighbor&&neighbor.combination!==c.combination;
  }));
});

test('selection keeps exterior edges and cancels internal grid lines', () => {
  const boundary=eventBoundary(frame([cell,{...cell,longitude:122.375}]),'CE-001');
  assert.equal(boundary.features.length,6);
  assert.ok(!boundary.features.some(f=>f.geometry.coordinates.every(p=>p[0]===122.25)));
});

test('fraction colors vary monotonically within a category and saturate at legend endpoints', () => {
  const colors=[0.25,0.35,0.5,0.65,0.75].map(affectedFraction=>gridGeoJSON(frame([{...cell,affectedFraction}])).features[0].properties.color);
  assert.equal(new Set(colors).size,5);
  const brightness=colors.map(color=>parseInt(color.slice(1,3),16));
  assert.deepEqual([...brightness].sort((a,b)=>b-a),brightness);
  assert.equal(gridGeoJSON(frame([{...cell,affectedFraction:1}])).features[0].properties.color,colors[4]);
});
