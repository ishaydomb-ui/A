/**
 * Writes the current catalogue to an .xlsx file.
 * Same workbook the catalogue export endpoint serves.
 */
import { resolve } from 'node:path';
import type { Locale } from '@med/shared';
import { pool } from '../db/pool.js';
import { buildCatalogueWorkbook } from '../services/catalogueExport.js';

const target = resolve(process.argv[2] ?? './catalogue.xlsx');
const locale = (process.argv[3] as Locale) ?? 'en';

const workbook = await buildCatalogueWorkbook({ locale });
await workbook.xlsx.writeFile(target);
console.log(`written: ${target}`);
await pool.end();
process.exit(0);
