import type {Values} from '../types';
export function increment(values:Values,id:string):Values {
 const old=values[id];
 const count=typeof old==='number' && Number.isInteger(old)?old:0;
 return {...values,[id]:Math.min(count+1,1000000)};
}
