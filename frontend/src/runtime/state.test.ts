import {describe,it,expect} from 'vitest';
import {increment} from './state';
describe('counter runtime',()=>{
 it('increments without mutating other component state',()=>{const before={count:2,note:'private'};expect(increment(before,'count')).toEqual({count:3,note:'private'});expect(before.count).toBe(2);});
 it('initializes missing state and respects the backend bound',()=>{expect(increment({},'count')).toEqual({count:1});expect(increment({count:1000000},'count')).toEqual({count:1000000});});
});
