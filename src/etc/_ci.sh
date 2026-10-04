#!/bin/bash

# Check new patch (GitLab source, GitHub ur_repo):
get_date_gl() {
	local project_path
	project_path=$(echo "$1" | sed 's|/|%2F|g')
	json=$(wget -qO- "https://gitlab.com/api/v4/projects/${project_path}/releases")
	case "$2" in
		latest)
			updated_at=$(echo "$json" | jq -r 'first(.[] | select(.tag_name | test("-dev") | not) | select(.assets.links[] | .name | test("'"$3"'")) | .released_at)')
			;;
		prerelease)
			updated_at=$(echo "$json" | jq -r 'first(.[] | select(.tag_name | test("-dev")) | select(.assets.links[] | .name | test("'"$3"'")) | .released_at)')
			;;
		*)
			updated_at=$(echo "$json" | jq -r 'first(.[] | select(.tag_name == "'"$2"'") | select(.assets.links[] | .name | test("'"$3"'")) | .released_at)')
			;;
	esac
	echo "$updated_at"
}

get_date_gh() {
	json=$(wget -qO- "https://api.github.com/repos/$1/releases")
	updated_at=$(echo "$json" | jq -r 'first(.[] | .assets[] | select(.name | test("'"$3"'")) | .updated_at)')
	echo "$updated_at"
}

checker(){
	local date1 date2 date1_sec date2_sec repo=$1 ur_repo=$repository check=$3
	date1=$(get_date_gl "$repo" "$2" "^(.*\\\.jar|.*\\\.rvp|.*\\\.mpp)$")
	date2=$(get_date_gh "$ur_repo" "all" "$check")
	[[ "$date1" == "null" ]] && date1=""
	[[ "$date2" == "null" ]] && date2=""
	if [ -z "$date1" ]; then
		echo -e "\e[31mCould not get date from GitLab for $repo\e[0m"
		return 1
	fi
	date1_sec=$(date -d "$date1" +%s)
	if [ -z "$date2" ]; then
		echo "new_patch=1" >> $GITHUB_OUTPUT
		echo -e "\e[32mNew patch, building...\e[0m"
		return
	fi
	date2_sec=$(date -d "$date2" +%s)
	if [ "$date1_sec" -gt "$date2_sec" ]; then
		echo "new_patch=1" >> $GITHUB_OUTPUT
		echo -e "\e[32mNew patch, building...\e[0m"
	else
		echo "new_patch=0" >> $GITHUB_OUTPUT
		echo -e "\e[32mOld patch, not build.\e[0m"
	fi
}
checker $1 $2 $3
