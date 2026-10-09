import { Profile, ProfileJob, RegisteredUser } from "./api";

export function indexProfiles(profiles: Profile[], jobs: ProfileJob[] = [], users: RegisteredUser[] = []) {
  const profilesById = new Map<string, Profile>();
  const usersById = new Map<string, RegisteredUser>();
  const jobsByProfileAndKind = new Map<string, ProfileJob>();
  const countsByOwner = new Map<string, number>();
  for (const profile of profiles) {
    if (!profilesById.has(profile.id)) profilesById.set(profile.id, profile);
    if (profile.owner_id) countsByOwner.set(profile.owner_id, (countsByOwner.get(profile.owner_id) ?? 0) + 1);
  }
  for (const user of users) {
    if (!usersById.has(user.id)) usersById.set(user.id, user);
  }
  for (const job of jobs) {
    const key = `${job.profile_id}:${job.kind}`;
    // API order is significant: match the first job, as Array.find did.
    if (!jobsByProfileAndKind.has(key)) jobsByProfileAndKind.set(key, job);
  }
  return { profilesById, usersById, jobsByProfileAndKind, countsByOwner };
}
