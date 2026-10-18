import React from 'react';
const GRADE = { A_Plus: ['accent', 'A+'], Alert_2R: ['warning-outline', '2R alert'], Pass: ['neutral', 'Pass'] };
const MODE = { paper: ['outline', 'Paper'], practice: ['warning', 'Practice'], combine: ['danger', 'Combine'] };
const SIDE = { long: ['bull', 'Long'], short: ['bear', 'Short'] };
export function Badge({ variant = 'neutral', grade, mode, side, dot, children, className }) {
  let v = variant, c = children;
  if (grade && GRADE[grade]) { v = GRADE[grade][0]; c = c || GRADE[grade][1]; }
  if (mode && MODE[mode]) { v = MODE[mode][0]; c = c || MODE[mode][1]; }
  if (side && SIDE[side]) { v = SIDE[side][0]; c = c || SIDE[side][1]; }
  return <span className={'fse-badge fse-badge--' + v + (className ? ' ' + className : '')}>{dot && <span className="fse-badge__dot" />}{c}</span>;
}