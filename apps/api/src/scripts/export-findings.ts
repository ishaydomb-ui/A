/**
 * Writes the open data-quality findings to an .xlsx file.
 * The same workbook the Review screen offers as a download.
 */
import { resolve } from 'node:path';
import { pool } from '../db/pool.js';
import { buildFindingsWorkbook } from '../services/findingsExport.js';

const target = resolve(process.argv[2] ?? './data-quality-findings.xlsx');

const workbook = await buildFindingsWorkbook();
await workbook.xlsx.writeFile(target);
console.log(`written: ${target}`);
await pool.end();
process.exit(0);
