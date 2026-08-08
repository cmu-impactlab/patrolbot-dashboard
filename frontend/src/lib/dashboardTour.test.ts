import { afterEach, describe, expect, it } from "vitest";
import { consumeTourUrl, shouldStartTourFromUrl, tourUrl } from "./dashboardTour";

function at(url: string) { history.replaceState({}, "", url); }

afterEach(() => at("/"));

describe("dashboard tour URL handoff", () => {
  it("removes replay and old guide routing from a tour link", () => {
    at("/dashboard/?replay=4&guide=1&mode=review");
    expect(tourUrl()).toBe("/dashboard/?mode=review&tour=1");
  });

  it("accepts the new trigger and redirects old guide bookmarks into the tour", () => {
    at("/?tour=1");
    expect(shouldStartTourFromUrl()).toBe(true);
    at("/?guide=1");
    expect(shouldStartTourFromUrl()).toBe(true);
  });

  it("consumes the one-shot trigger without losing unrelated parameters", () => {
    at("/dashboard/?tour=1&mode=review#top");
    consumeTourUrl();
    expect(`${location.pathname}${location.search}${location.hash}`).toBe("/dashboard/?mode=review#top");
  });
});
