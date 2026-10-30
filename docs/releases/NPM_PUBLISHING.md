# Publishing the npm beta

Package: **`@mahekara28/context-relay`**. Registry: **https://registry.npmjs.org/**.
Publisher: npm user **`mahekara28`**, independently authenticated from GitHub.
The unscoped `context-relay` package belongs to another project.

## Release procedure

1. Use an isolated npm profile for this account. In the maintainer's dedicated
   workspace, use its `mahek-npm` launcher for login, identity checks and publishing.
   Never borrow another account's npm credentials or commit `.npmrc` tokens.
2. Confirm `npm whoami` returns `mahekara28` in that profile. Complete npm's web
   login / two-factor challenge if requested. A GitHub PAT is not npm access.
3. Keep `package.json`, the lockfile, runtime version and docs aligned. Check that
   the version has not already been published. Published versions cannot be replaced.
4. Run `npm run typecheck`, `npm test`, `npm run test:package`,
   `npm run test:browser` and `npm run test:site`. Review the final PR's CI on Linux,
   macOS and Windows. The package gate uses anonymous, temporary npm config, cache
   and global prefix, exercises the installed command and npm exec/npx, then
   verifies the service, UI, demo, handoff and persistence.
5. Pack once, inspect the file list and preserve that exact tarball:

   ```sh
   npm pack --json --pack-destination /path/to/release-artifacts
   npm publish /path/to/release-artifacts/mahekara28-context-relay-0.3.0-beta.1.tgz --dry-run --access public --tag beta
   ```

6. Publish the inspected tarball using the isolated authenticated npm launcher:

   ```sh
   npm publish /path/to/release-artifacts/mahekara28-context-relay-0.3.0-beta.1.tgz --access public --tag beta
   ```

   `publishConfig` defaults to the public npm registry, public access and the
   `beta` tag. Keep prereleases on `beta`; promoting a stable version to `latest`
   is a separate release decision. Never put credentials or OTPs in Git history.

7. Read registry metadata and compare `dist.integrity` with the inspected tarball.
   In an anonymous temporary environment, verify global installation plus
   `npx @mahekara28/context-relay@beta --version`, `--help`, and a demo/UI lifecycle.
   Only then merge/deploy documentation that advertises registry installation.

For subsequent automated releases, npm supports [trusted publishing from GitHub
Actions](https://docs.npmjs.com/trusted-publishers/). Configure that trust explicitly
on npm before enabling a publishing workflow; the current npm CI builds and tests
artifacts and does not publish automatically.

## Consumer commands

```sh
npm install --global @mahekara28/context-relay@beta
relay ui
```

```sh
npx @mahekara28/context-relay@beta ui
```

Replace `@beta` with `@0.3.0-beta.1` to pin this release. Node 22.16+ is required;
Git, Python, a frontend build and a model API key are not installation prerequisites.
