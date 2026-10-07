import config, { defaultAppId } from '../../app.config';

describe('app.config', () => {
  it('derives an app identifier valid on both iOS and Android', () => {
    // Lower-case reverse-DNS, each part starting with a letter, only letters and digits.
    expect(defaultAppId).toMatch(/^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$/);
    expect(config.ios?.bundleIdentifier).toBe(process.env.APP_ID || defaultAppId);
    expect(config.android?.package).toBe(config.ios?.bundleIdentifier);
  });
});
