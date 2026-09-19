FROM rust:1.98.1-slim-bookworm@sha256:ebd900bae66fd508b466cef82d64a83a5fb34682e4c8b2797a42908bddc95a57 AS build

WORKDIR /source
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY tests ./tests
RUN cargo build --release --locked --bin queue-qualification

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

RUN useradd --create-home --uid 10001 qualification
COPY --from=build /source/target/release/queue-qualification /usr/local/bin/queue-qualification
USER qualification
ENTRYPOINT ["/usr/local/bin/queue-qualification"]
