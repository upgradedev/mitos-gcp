// The boundary, as a picture rather than a table.
//
// The thing to understand in sixty seconds: the reader cannot write, and the
// refusal that matters comes from Google IAM, outside this process, rather
// than from an if statement this service could change its mind about.
//
// Only one of the three services can be measured from here. The writer and the
// evaluator are deployed to refuse anonymous callers, and a fetch to them from
// a browser would fail in a way that is indistinguishable from a network
// error, so this page does not try. It draws the asymmetry instead: one
// identity read from the service, two named but unverified. Their refusal is
// the evidence, and pretending to have collected it would be the same failure
// as inventing a number.

import { useEffect, useState } from "react";
import { getCatalog, getConfig, getIdentity, load } from "../api/client";
import type { Catalog, Config, Identity, Loaded } from "../api/types";
import "./boundary-view.css";

export function BoundaryView() {
  const [identity, setIdentity] = useState<Loaded<Identity>>({
    status: "loading",
  });
  const [catalog, setCatalog] = useState<Loaded<Catalog>>({ status: "loading" });
  const [config, setConfig] = useState<Loaded<Config>>({ status: "loading" });

  useEffect(() => {
    let live = true;
    load(getIdentity).then((r) => live && setIdentity(r));
    load(getCatalog).then((r) => live && setCatalog(r));
    load(getConfig).then((r) => live && setConfig(r));
    return () => {
      live = false;
    };
  }, []);

  return (
    <section className="mitos-boundary">
      <header className="mitos-boundary__head">
        <h1 className="mitos-boundary__title">The boundary</h1>
        <p className="mitos-boundary__lede">
          Three services, three identities, and only one of them holds a
          credential that can write. Everything below is read from the service
          you are looking at. Where it could not check something, it says so
          instead of filling it in.
        </p>
      </header>

      <div className="mitos-boundary__body">
        {identity.status === "loading" ? (
          <div className="mitos-boundary__state">
            <div className="mitos-boundary__state-title">
              Reading this service&rsquo;s identity.
            </div>
            <div className="mitos-boundary__state-body">
              Asking GET /identity.
            </div>
          </div>
        ) : identity.status !== "ok" ? (
          <IdentityFailure
            absent={identity.status === "absent"}
            detail={identity.detail}
          />
        ) : (
          <Boundary
            identity={identity.value}
            catalog={catalog}
            config={config}
          />
        )}
      </div>
    </section>
  );
}

function IdentityFailure({
  absent,
  detail,
}: {
  absent: boolean;
  detail: string;
}) {
  return (
    <div className="mitos-boundary__state">
      <div className="mitos-boundary__state-title">
        {absent
          ? "This build does not serve GET /identity."
          : "The identity of this service could not be read."}
      </div>
      <div className="mitos-boundary__state-body">
        Every claim on this page comes from that one response, so with it
        missing there is nothing here that can honestly be shown. One known
        cause: the deployed content policy sets default-src to none and
        declares no connect-src, and a browser refuses that request before it
        is sent.
      </div>
      <div className="mitos-boundary__state-detail">{detail}</div>
    </div>
  );
}

function Boundary({
  identity,
  catalog,
  config,
}: {
  identity: Identity;
  catalog: Loaded<Catalog>;
  config: Loaded<Config>;
}) {
  const tools = Object.entries(identity.may_call_write_tools);
  const refusedTools = tools.filter(([, allowed]) => allowed === false);
  const credential = identity.spec_repo_write_credential;

  return (
    <>
      <p className="mitos-boundary__note">
        The reader is the service serving this page. It read its own identity
        and its own credential, and both refused it. The first refusal is a
        decision this process makes. The second is not, and that is the one
        that holds.
      </p>

      {/* The picture. One lane per service, each lane a road that either
          reaches the spec repository or stops at a gate. */}
      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">
          Who can put bytes in the spec repository
        </div>

        <div className="mitos-lane is-measured">
          <div className="mitos-lane__who">
            <span className="mitos-lane__name">
              The reader &middot; the service serving this page
            </span>
            <span className="mitos-badge tone-refusal">measured just now</span>
          </div>
          <div className="mitos-lane__as">{identity.running_as}</div>

          <div className="mitos-lane__road">
            <div className="mitos-gate tone-refusal">
              <div className="mitos-gate__where">
                gate 1 &middot; inside this process
              </div>
              <div className="mitos-gate__what">
                Every write tool is refused before it runs.
              </div>
              <div className="mitos-gate__verdict tone-refusal">
                {refusedTools.length} of {tools.length} refused
              </div>
            </div>

            <div className="mitos-gate tone-refusal">
              <div className="mitos-gate__where">
                gate 2 &middot; outside this process
              </div>
              <div className="mitos-gate__what">
                Google IAM refuses this identity the write credential.
              </div>
              <div className="mitos-gate__verdict tone-refusal">
                {credential.reachable
                  ? "credential reachable"
                  : credential.detail ?? "refused"}
              </div>
            </div>

            <div className="mitos-gate tone-quiet">
              <div className="mitos-gate__where">the spec repository</div>
              <div className="mitos-gate__what">
                Bytes only arrive here through the writer, after a person
                approves an exact plan.
              </div>
              <div className="mitos-gate__verdict tone-quiet">
                not reached by this service
              </div>
            </div>
          </div>

          <div className="mitos-lane__end">
            <b>The reader cannot write.</b> It stops at gate 1 by its own
            decision and at gate 2 by someone else&rsquo;s.
          </div>
        </div>

        <div className="mitos-lane is-unverified">
          <div className="mitos-lane__who">
            <span className="mitos-lane__name">
              The writer &middot; a separate service, a separate identity
            </span>
            <span className="mitos-badge tone-quiet">not checked here</span>
          </div>
          <div className="mitos-lane__as">
            identity not readable from this page
          </div>
          <div className="mitos-lane__road">
            <div className="mitos-gate tone-quiet mitos-gate__span">
              <div className="mitos-gate__where">why this is blank</div>
              <div className="mitos-gate__what">
                The writer refuses anonymous callers, so this page cannot read
                its identity. From a browser, that refusal and an ordinary
                network failure look the same, so nothing is claimed here.
                Its refusing is the evidence; a badge invented on this page
                would not be.
              </div>
            </div>
          </div>
          <div className="mitos-lane__end">
            What this service does say about it, in the refusal text below:
            a governed write &ldquo;runs only in the writer service after a
            human approves a content-addressed plan&rdquo;.
          </div>
        </div>

        <div className="mitos-lane is-unverified">
          <div className="mitos-lane__who">
            <span className="mitos-lane__name">
              The evaluator &middot; a separate service, a separate identity
            </span>
            <span className="mitos-badge tone-quiet">not checked here</span>
          </div>
          <div className="mitos-lane__as">
            identity not readable from this page
          </div>
          <div className="mitos-lane__road">
            <div className="mitos-gate tone-quiet mitos-gate__span">
              <div className="mitos-gate__where">why this is blank</div>
              <div className="mitos-gate__what">
                Also refuses anonymous callers. It judges drafts and is not on
                the path that writes bytes, but this page has not verified
                either statement and does not present them as measured.
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* The claim the picture exists to make. */}
      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">
          Two refusals, and only one of them matters
        </div>
        <div className="mitos-locks">
          <div className="mitos-lock">
            <div className="mitos-lock__rank">gate 1</div>
            <div className="mitos-lock__name">
              This process decides, so this process could decide otherwise.
            </div>
            <div className="mitos-lock__body">
              may_call_write_tools is enforced in a callback inside the running
              agent. It is real, and it is code in this service. A change to
              this service could flip it.
            </div>
          </div>
          <div className="mitos-lock is-loadbearing">
            <div className="mitos-lock__rank">gate 2 &middot; load-bearing</div>
            <div className="mitos-lock__name">
              Google IAM decides, and this service cannot argue with it.
            </div>
            <div className="mitos-lock__body">
              The write credential lives in Secret Manager and this identity is
              refused access to it. No change to this code grants it. That is
              why the refusal below is the control that counts.
            </div>
          </div>
        </div>
      </div>

      {/* The refusal, verbatim. */}
      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">
          The refusal, as Google returned it
        </div>
        <div className="mitos-quote">
          <div className="mitos-refusal-text">
            {credential.message ??
              "The service reported no message with this refusal."}
          </div>
          <span className="mitos-quote__source">
            GET /identity, spec_repo_write_credential. reachable:{" "}
            {String(credential.reachable)}
            {credential.detail === undefined ? "" : `, detail: ${credential.detail}`}
          </span>
        </div>
        {identity.note === undefined ? null : (
          <div className="mitos-quote">
            <div className="mitos-refusal-text">{identity.note}</div>
            <span className="mitos-quote__source">
              GET /identity, note. This page did not write that sentence; the
              service did.
            </span>
          </div>
        )}
      </div>

      {/* The measured facts, named so they can be checked. */}
      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">
          What this service reports about itself
        </div>
        <div className="mitos-evidence">
          <Row k="role" v={identity.role} />
          <Row k="running as" v={identity.running_as} />
          <Row k="project" v={identity.project} />
          <Row k="model" v={identity.model} />
          <Row k="build" v={identity.build_sha} />
          {tools.map(([tool, allowed]) => (
            <div className="mitos-evidence__row" key={tool}>
              <span className="mitos-evidence__key">may call {tool}</span>
              <span className="mitos-evidence__val">
                <span
                  className={
                    allowed ? "mitos-badge tone-write" : "mitos-badge tone-refusal"
                  }
                >
                  {allowed ? "yes" : "no"}
                </span>
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* What the reader can do, so the picture is not only a list of noes. */}
      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">What the reader may reach</div>
        {config.status === "ok" ? (
          <div className="mitos-evidence">
            <Row k="read scope" v={config.value.read_scope.join("  ")} />
            <Row
              k="repositories it accepts webhooks from"
              v={config.value.webhook_repositories.join("  ")}
            />
            <Row
              k="reads per run"
              v={`at most ${config.value.max_reads_per_run}`}
            />
            <Row
              k="bytes per read"
              v={`at most ${config.value.max_bytes_per_read}`}
            />
          </div>
        ) : (
          <Unavailable
            what="GET /config"
            absent={config.status === "absent"}
            detail={config.status === "loading" ? "still loading" : config.detail}
          />
        )}
      </div>

      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">
          How many companions declare a write at all
        </div>
        {catalog.status === "ok" ? (
          <CatalogWrites catalog={catalog.value} />
        ) : (
          <Unavailable
            what="GET /catalog"
            absent={catalog.status === "absent"}
            detail={
              catalog.status === "loading" ? "still loading" : catalog.detail
            }
          />
        )}
      </div>

      <div className="mitos-boundary__section">
        <div className="mitos-boundary__label">
          What this page could not check
        </div>
        <ul className="mitos-unchecked">
          <li>
            The writer&rsquo;s identity, and whether it holds the credential.
            It refuses anonymous callers and this page did not ask, because a
            refused request and a blocked one are the same from here.
          </li>
          <li>The evaluator&rsquo;s identity, for the same reason.</li>
          <li>
            Whether the credential Google refuses this reader would in fact
            permit a write if some other identity held it. Only the refusal is
            observed.
          </li>
          <li>
            Anything about a service this page did not fetch. There are three
            services named here and one of them answered.
          </li>
        </ul>
      </div>
    </>
  );
}

function CatalogWrites({ catalog }: { catalog: Catalog }) {
  const declaring = catalog.companions.filter(
    (companion) => companion.writes.length > 0
  );
  return (
    <>
      <p className="mitos-boundary__note">
        {declaring.length} of {catalog.companions.length}{" "}
        {declaring.length === 1 ? "companions declares" : "companions declare"}{" "}
        that it writes anything at all. Declaring it is not holding the
        credential: the refusal above applies to every one of them while they
        run in this service.
      </p>
      <div className="mitos-evidence">
        {catalog.companions.map((companion) => (
          <div className="mitos-evidence__row" key={companion.name}>
            <span className="mitos-evidence__key">{companion.name}</span>
            <span className="mitos-evidence__val">
              {companion.writes.length === 0 ? (
                <span className="mitos-badge tone-quiet">declares no write</span>
              ) : (
                <span className="mitos-badge tone-refusal">
                  declares {companion.writes.join(", ")}
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="mitos-evidence__row">
      <span className="mitos-evidence__key">{k}</span>
      <span className="mitos-evidence__val">{v}</span>
    </div>
  );
}

function Unavailable({
  what,
  absent,
  detail,
}: {
  what: string;
  absent: boolean;
  detail: string;
}) {
  return (
    <div className="mitos-boundary__state-body">
      {absent
        ? `${what} is not served by this build, so this section is unknown rather than empty.`
        : `${what} could not be read, so this section is unknown rather than empty.`}
      <div className="mitos-boundary__state-detail">{detail}</div>
    </div>
  );
}

export default BoundaryView;
